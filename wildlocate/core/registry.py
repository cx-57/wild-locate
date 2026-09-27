"""Local accounts plus bundled and account-owned model registry."""

import csv
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import uuid

import wildlocate

from wildlocate.core.environment import get_user_data_dir
from wildlocate.core.regional import get_region


def connect():
    folder = get_user_data_dir() / 'accounts'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / 'accounts.sqlite3'
    db = sqlite3.connect(path)
    os.chmod(path, 0o600)
    db.execute('CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, salt BLOB NOT NULL, digest BLOB NOT NULL)')
    return db


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)


def normalize_username(username):
    if not isinstance(username, str):
        raise ValueError('Sign in to access your trained models.')
    username = username.strip().casefold()
    if not 3 <= len(username) <= 40 or not all(c.isalnum() or c in '_.-' for c in username):
        raise ValueError('Use 3–40 letters, numbers, dots, dashes or underscores for your username.')
    return username


def account_data_dir(username):
    """Resolve an authenticated local account to a stable, filesystem-safe folder."""
    username = normalize_username(username)
    identifier = hashlib.sha256(username.encode('utf-8')).hexdigest()
    return get_user_data_dir() / 'accounts' / identifier


def authenticate(username, password, create=False):
    username = normalize_username(username)
    if len(password) > 256:
        raise ValueError('Use a password of at most 256 characters.')
    if create and len(password) < 8:
        raise ValueError('Use at least 8 characters for your password.')
    db = connect()
    try:
        if create:
            salt = os.urandom(16)
            try:
                with db:
                    db.execute('INSERT INTO users VALUES (?, ?, ?)', (username, salt, password_hash(password, salt)))
            except sqlite3.IntegrityError:
                raise ValueError('That username is already taken on this computer.') from None
        else:
            record = db.execute('SELECT salt, digest FROM users WHERE username = ?', (username,)).fetchone()
            salt, expected = record if record else (bytes(16), bytes(64))
            valid = hmac.compare_digest(password_hash(password, salt), expected)
            if not valid or record is None:
                raise ValueError('The username or password doesn’t match. Try again.')
    finally:
        db.close()
    return username



BUNDLED_DATA = Path(wildlocate.__file__).resolve().parent / "bundled"


@dataclass(frozen=True)
class ModelRecord:
    id: str
    species: str
    model_path: Path
    metrics_path: Path
    features_path: Path
    custom: bool = False
    created_at: str = ""
    region: str = "MA"

    def metrics(self):
        return json.loads(self.metrics_path.read_text(encoding="utf-8"))


def custom_root(username):
    return account_data_dir(username) / "models"


def job_path(job_id, *, username=None):
    return _child(account_data_dir(username) / "training", job_id)


def _child(root, identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
        raise ValueError("Invalid model identifier.")
    root = root.resolve()
    path = root / identifier
    if path.resolve().parent != root or path.is_symlink():
        raise ValueError("Model path is outside its storage folder.")
    return path


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def complete(record):
    """Inspect metadata and CSV schema without unpickling a model in the UI."""
    try:
        if any(not p.is_file() or p.stat().st_size == 0 or p.is_symlink()
               for p in (record.model_path, record.metrics_path, record.features_path)):
            return False
        metadata = record.metrics()
        if get_region(metadata.get("region", "MA")).code != record.region:
            return False
        if record.region != "MA" and (metadata.get("region") != record.region or metadata.get("feature_schema") != "regional-raster-v1"):
            return False
        predictors = metadata["predictor_names"]
        if not isinstance(predictors, list) or not predictors or not all(isinstance(p, str) for p in predictors):
            return False
        if metadata["species"] != record.species or not metadata.get("selected_model"):
            return False
        with record.features_path.open(newline="", encoding="utf-8") as stream:
            rows = csv.reader(stream)
            header = next(rows)
            if not set(predictors).issubset(header) or not next(rows, None):
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError, StopIteration, csv.Error):
        return False


def custom_record(identifier, *, username=None):
    username = normalize_username(username)
    folder = _child(custom_root(username), identifier)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Invalid model manifest.")
    name = manifest.get("species")
    if manifest.get("version") != 1 or not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise ValueError("Invalid model manifest.")
    if manifest.get("owner") != username:
        raise ValueError("This model is not available to this account.")
    return ModelRecord(identifier, name, folder / "model.joblib", folder / "metrics.json",
                       folder / "features.csv", True, str(manifest.get("created_at", "")), get_region(manifest.get("region", "MA")).code)


def list_models(region="MA", *, username=None):
    region = get_region(region).code
    records = []
    for metrics_path in sorted(BUNDLED_DATA.glob("*_metrics.json")):
        try:
            metadata = json.loads(metrics_path.read_text(encoding="utf-8"))
            name = metadata["species"]
            slug = metrics_path.name.removesuffix("_metrics.json")
            if not isinstance(name, str) or not name.strip():
                continue
            record = ModelRecord(
                f"bundled:{slug}",
                name,
                BUNDLED_DATA / f"{slug}.joblib",
                metrics_path,
                BUNDLED_DATA / f"{slug}_features.csv",
            )
            if complete(record):
                records.append(record)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if username is not None and custom_root(username).exists():
        for folder in sorted(custom_root(username).iterdir()):
            try:
                record = custom_record(folder.name, username=username)
                if complete(record):
                    records.append(record)
            except (OSError, ValueError, TypeError):
                continue
    return [record for record in records if record.region == region]


def _activation(username):
    if username is None:
        return {}
    try:
        data = json.loads((account_data_dir(username) / "active_models.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def available_models(region="MA", *, username=None):
    records = list_models(region, username=username)
    available = {r.species.casefold(): r for r in records if not r.custom}
    active = _activation(username)
    for record in records:
        if record.custom and active.get(_activation_key(record)) == record.id:
            available[record.species.casefold()] = record
    return available


def available_species(region="MA", *, username=None):
    return tuple(record.species for record in available_models(region, username=username).values())


def resolve_model(species, region="MA", *, username=None):
    record = available_models(region, username=username).get(species.strip().casefold())
    if record is None:
        raise FileNotFoundError(f"No trained model found for {species}. Enable a complete model in Manage species.")
    return record


def _activation_key(record):
    name = record.species.casefold()
    return name if record.region == "MA" else f"{record.region}:{name}"


def enable_model(identifier, *, username=None):
    username = normalize_username(username)
    from wildlocate.core.regional import REGIONS
    records = {r.id: r for region in REGIONS for r in list_models(region, username=username)}
    if identifier not in records:
        raise ValueError("This model is incomplete or no longer available.")
    record = records[identifier]
    active = _activation(username)
    if record.custom:
        active[_activation_key(record)] = record.id
    else:
        active.pop(_activation_key(record), None)
    atomic_json(account_data_dir(username) / "active_models.json", active)


def delete_model(identifier, *, username=None):
    record = custom_record(identifier, username=username)
    active = _activation(username)
    if active.get(_activation_key(record)) == identifier:
        active.pop(_activation_key(record))
        atomic_json(account_data_dir(username) / "active_models.json", active)
    shutil.rmtree(_child(custom_root(username), identifier))


def cleanup_job(identifier, *, username=None):
    path = job_path(identifier, username=username)
    if path.exists():
        shutil.rmtree(path)
