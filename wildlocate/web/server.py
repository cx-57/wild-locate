"""Loopback-only browser app backed by Wild-Locate's existing local services."""

import json
import math
import mimetypes
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from wildlocate.core.registry import authenticate
from wildlocate.core.registry import (
    available_models,
    available_species,
    delete_model,
    enable_model,
    list_models,
)
from wildlocate.core.regional import REGIONS, get_region
from wildlocate.core.observations import species_suggestions
from wildlocate.core.registry import cleanup_job

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT.parent / "gui"
ASSETS = {
    "/": ROOT / "index.html",
    "/style.css": ROOT / "style.css",
    "/app.js": ROOT / "app.js",
}
for name in ("leaflet.js", "leaflet.css"):
    ASSETS["/vendor/" + name] = VENDOR / name


class JobManager:
    """Own one cancellable prediction subprocess at a time."""

    def __init__(self, command=None, worker_mode="predict"):
        self.command = command
        self.worker_mode = worker_mode
        self.lock = threading.Lock()
        self.job = None
        self.process = None
        self.closed = False

    def start(self, payload, username=None):
        with self.lock:
            if self.closed or (self.job and self.job["status"] == "running"):
                raise ValueError("An analysis is already running or the server is stopping.")
            command = list(self.command) if self.command else [
                sys.executable, "-u", "-m", "wildlocate.core.worker", self.worker_mode,
            ]
            if self.command is None and username:
                command.extend(["--account", username])
            env = dict(os.environ)
            env["PYTHONPATH"] = os.pathsep.join(
                filter(None, (str(ROOT.parents[1]), env.get("PYTHONPATH")))
            )
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            job = {"id": uuid.uuid4().hex, "status": "running"}
            self.job, self.process = job, process
            threading.Thread(
                target=self._collect, args=(process, job, payload), daemon=True
            ).start()
            return dict(job)

    def _collect(self, process, job, payload):
        try:
            stdout, _stderr = process.communicate(
                json.dumps(payload, allow_nan=False) + "\n", timeout=1800
            )
            response = json.loads(stdout)
            if process.returncode or not isinstance(response, dict):
                raise ValueError("Worker failed")
            if "error" in response:
                update = {
                    "status": "error",
                    "error": str(response["error"]),
                    "code": response.get("code"),
                }
            elif isinstance(response.get("result"), dict):
                json.dumps(response["result"], allow_nan=False)
                update = {"status": "complete", "result": response["result"]}
            else:
                raise ValueError("Invalid worker response")
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            update = {
                "status": "error",
                "error": "Analysis timed out. Try a smaller area.",
            }
        except Exception:
            update = {
                "status": "error",
                "error": (
                    "The analysis process could not complete. "
                    "Check your local models and environmental data."
                ),
            }
        with self.lock:
            if self.job is job and job["status"] == "running":
                job.update(update)

    def status(self, identifier):
        with self.lock:
            if not self.job or self.job["id"] != identifier:
                raise KeyError(identifier)
            return dict(self.job)

    def cancel(self, identifier):
        with self.lock:
            if not self.job or self.job["id"] != identifier:
                raise KeyError(identifier)
            if self.job["status"] == "running":
                self.job["status"] = "cancelled"
                if self.process and self.process.poll() is None:
                    self.process.kill()
                    self.process.wait(timeout=10)
            return dict(self.job)

    def reset(self):
        with self.lock:
            if self.job and self.job.get("status") == "running":
                self.job["status"] = "cancelled"
            if self.process and self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=10)
            self.job = None
            self.process = None

    def close(self):
        self.reset()
        with self.lock:
            self.closed = True


class TrainingManager:
    """Keep one persistent training worker so resolve/prepare/train share state."""

    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.job = None
        self.username = None
        self.closed = False

    def _cleanup_workspace(self, job_id, username):
        if not job_id or not username:
            return
        try:
            cleanup_job(job_id, username=username)
        except (OSError, ValueError):
            pass

    def reset(self):
        with self.lock:
            process = self.process
            job = self.job
            username = self.username
            if job and job.get("status") == "running":
                job["status"] = "cancelled"
            self.process = None
            self.job = None
            self.username = None
        if process and process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
        if job:
            self._cleanup_workspace(job.get("id"), username)

    def close(self):
        self.reset()
        with self.lock:
            self.closed = True

    def start(self, region, username, query):
        if not isinstance(query, str) or not query.strip() or len(query.strip()) > 100:
            raise ValueError("Enter a species name up to 100 characters.")
        region = get_region(region).code
        self.reset()
        with self.lock:
            if self.closed:
                raise ValueError("The server is stopping.")
            job_id = uuid.uuid4().hex
            env = dict(os.environ)
            env["PYTHONPATH"] = os.pathsep.join(
                filter(None, (str(ROOT.parents[1]), env.get("PYTHONPATH")))
            )
            command = [
                sys.executable,
                "-u",
                "-m",
                "wildlocate.core.worker",
                "train",
                "--job-id",
                job_id,
                "--region",
                region,
                "--account",
                username,
            ]
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
            )
            self.process = process
            self.username = username
            self.job = {
                "id": job_id,
                "region": region,
                "status": "running",
                "action": "resolve",
                "message": "Finding the species on iNaturalist…",
                "logs": [],
            }
            threading.Thread(
                target=self._read_stdout, args=(process, self.job), daemon=True
            ).start()
            threading.Thread(
                target=self._read_stderr, args=(process, self.job), daemon=True
            ).start()
            self._send_locked({"action": "resolve", "query": query.strip()})
            return self._snapshot_locked()

    def _send_locked(self, payload):
        if not self.process or self.process.poll() is not None or not self.process.stdin:
            raise ValueError("The training process is no longer running.")
        self.process.stdin.write(json.dumps(payload, allow_nan=False) + "\n")
        self.process.stdin.flush()

    def _snapshot_locked(self):
        if not self.job:
            raise KeyError("training")
        snapshot = dict(self.job)
        snapshot["logs"] = list(self.job.get("logs", []))
        if isinstance(snapshot.get("result"), dict):
            snapshot["result"] = dict(snapshot["result"])
        return snapshot

    def _read_stdout(self, process, job):
        if not process.stdout:
            return
        for line in process.stdout:
            try:
                event = json.loads(line)
                kind = event.get("event")
                if not isinstance(event, dict) or not isinstance(kind, str):
                    raise ValueError
            except Exception:
                with self.lock:
                    if self.process is process and self.job is job and job["status"] == "running":
                        job.update(
                            status="error",
                            error="Training returned an unreadable response. Please try again.",
                        )
                continue

            with self.lock:
                if self.process is not process or self.job is not job:
                    return
                if kind == "progress":
                    job["message"] = str(event.get("message", "Working…"))
                    continue
                if kind == "error":
                    job.update(
                        status="error",
                        error=str(event.get("message", "Training failed.")),
                        code=event.get("code"),
                    )
                    continue
                result = {k: v for k, v in event.items() if k != "event"}
                job.update(status=kind, result=result)
                job.pop("error", None)
                job.pop("code", None)
                messages = {
                    "resolved": "Species found. Confirm it and check the available data.",
                    "prepared": "Species data is ready. You can start training.",
                    "initialized": "Environmental datasets are ready. Check the species data again.",
                    "completed": "Training complete. Review the model before enabling it.",
                }
                job["message"] = messages.get(kind, "Ready.")
                if kind == "completed" and process.stdin:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return
        with self.lock:
            if self.process is process and self.job is job and job.get("status") == "running":
                job.update(
                    status="error",
                    error="Training stopped unexpectedly. Your enabled models are unchanged.",
                )

    def _read_stderr(self, process, job):
        if not process.stderr:
            return
        for line in process.stderr:
            text = line.strip()
            if not text:
                continue
            with self.lock:
                if self.process is not process or self.job is not job:
                    return
                logs = job.setdefault("logs", [])
                logs.append(text)
                del logs[:-40]

    def status(self, identifier):
        with self.lock:
            if not self.job or self.job["id"] != identifier:
                raise KeyError(identifier)
            return self._snapshot_locked()

    def action(self, identifier, action):
        with self.lock:
            if not self.job or self.job["id"] != identifier:
                raise KeyError(identifier)
            if self.job["status"] == "running":
                raise ValueError("Wait for the current training step to finish.")
            if not self.process or self.process.poll() is not None:
                raise ValueError("The training session has ended. Start a new one.")
            allowed = {
                "prepare": {"resolved", "initialized", "error"},
                "train": {"prepared", "error"},
                "initialize": {"resolved", "initialized", "error"},
            }
            if action not in allowed or self.job["status"] not in allowed[action]:
                raise ValueError("That training step is not available yet.")
            self.job.update(status="running", action=action)
            self.job.pop("error", None)
            self.job.pop("code", None)
            self._send_locked({"action": action})
            return self._snapshot_locked()

    def cancel(self, identifier):
        with self.lock:
            if not self.job or self.job["id"] != identifier:
                raise KeyError(identifier)
            process = self.process
            username = self.username
            job_id = self.job["id"]
            self.job["status"] = "cancelled"
            self.job["message"] = "Training cancelled. Your enabled models are unchanged."
            snapshot = self._snapshot_locked()
        if process and process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
        self._cleanup_workspace(job_id, username)
        return snapshot


def validate_request(payload, username):
    if not isinstance(payload, dict) or set(payload) - {
        "species", "region", "latitude", "longitude", "radius_km"
    }:
        raise ValueError(
            "Supply species, state, latitude, longitude, and optionally radius."
        )
    region = payload.get("region", "MA")
    if not isinstance(region, str) or region not in REGIONS:
        raise ValueError("Choose a supported state.")
    if payload.get("species") not in available_species(region, username=username):
        raise ValueError("Choose an available species for this state.")
    for key, limit in (("latitude", 90), ("longitude", 180)):
        value = payload.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not -limit <= value <= limit
        ):
            raise ValueError(
                f"Enter a valid {key} between {-limit} and {limit}."
            )
    radius = payload.get("radius_km")
    if radius is not None and (
        isinstance(radius, bool) or radius not in (10, 25, 50)
    ):
        raise ValueError("Choose a radius of 10, 25, or 50 km.")
    return dict(payload, region=region)


def model_summaries(region, username):
    region = get_region(region).code
    active_ids = {
        record.id for record in available_models(region, username=username).values()
    }
    summaries = []
    for record in list_models(region, username=username):
        item = {
            "id": record.id,
            "species": record.species,
            "custom": record.custom,
            "source": "Custom" if record.custom else "Bundled",
            "enabled": record.id in active_ids,
            "created_at": record.created_at,
        }
        try:
            metrics = record.metrics()
            selected_name = metrics.get("selected_model", "Unknown Model")
            selected = next(
                (
                    row
                    for row in metrics.get("metrics_by_model", [])
                    if row.get("model") == selected_name
                ),
                {},
            )
            presence = int(metrics.get("presence_count", 0))
            background = int(metrics.get("background_count", 0))
            total = presence + background
            roc = selected.get("mean_roc_auc")
            pr = selected.get("mean_pr_auc")
            baseline = presence / total if total else None
            item.update(
                model=selected_name,
                presence_count=presence,
                background_count=background,
                folds=metrics.get("number_of_folds"),
                roc_auc=roc,
                pr_auc=pr,
                pr_reference=baseline,
                experimental=bool(
                    baseline is not None
                    and roc is not None
                    and pr is not None
                    and (roc <= 0.5 or pr <= baseline)
                ),
            )
        except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError):
            item["details_error"] = True
        summaries.append(item)
    return summaries


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_close(self):
        if hasattr(self, "jobs"):
            self.jobs.close()
        if hasattr(self, "deep_dives"):
            self.deep_dives.close()
        if hasattr(self, "training"):
            self.training.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_):
        pass

    def reply(self, status, value, content_type="application/json"):
        data = (
            value
            if isinstance(value, bytes)
            else json.dumps(value, allow_nan=False).encode()
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https://tile.openstreetmap.org; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def allowed(self, mutation=False):
        origin = self.server.origin
        expected_host = origin.split("://", 1)[1]
        if (
            self.headers.get("Host") != expected_host
            or self.headers.get("Origin", origin) != origin
        ):
            self.reply(
                403, {"error": "Use the local URL printed by Wild-Locate."}
            )
            return False
        if mutation and not secrets.compare_digest(
            self.headers.get("X-Wildlocate-Token", ""), self.server.token
        ):
            self.reply(
                403, {"error": "Reload the page to reconnect to Wild-Locate."}
            )
            return False
        return True

    def username(self):
        with self.server.session_lock:
            return self.server.username

    def require_auth(self):
        username = self.username()
        if username is None:
            self.reply(401, {"error": "Sign in to continue."})
            return None
        return username

    def config_payload(self):
        username = self.username()
        return {
            "token": self.server.token,
            "authenticated": username is not None,
            "username": username,
            "regions": [
                {
                    "code": code,
                    "name": region.name,
                    "center": region.center,
                    "species": (
                        list(available_species(code, username=username))
                        if username
                        else []
                    ),
                }
                for code, region in REGIONS.items()
            ],
        }

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 8192:
            self.reply(413, {"error": "Request is too large."})
            return None
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self.reply(415, {"error": "Send a JSON request."})
            return None
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self.reply(422, {"error": "Send a valid JSON object."})
            return None
        if not isinstance(payload, dict):
            self.reply(422, {"error": "Send a JSON object."})
            return None
        return payload

    def do_GET(self):
        if not self.allowed():
            return
        parsed = urlsplit(self.path)
        path = parsed.path

        if path == "/api/config":
            self.reply(200, self.config_payload())
            return

        if path == "/api/models":
            username = self.require_auth()
            if username is None:
                return
            region = parse_qs(parsed.query).get("region", ["MA"])[0]
            try:
                self.reply(
                    200,
                    {
                        "region": get_region(region).code,
                        "models": model_summaries(region, username),
                    },
                )
            except ValueError as exc:
                self.reply(422, {"error": str(exc)})
            return

        if path.startswith("/api/jobs/"):
            if self.require_auth() is None:
                return
            try:
                self.reply(
                    200, self.server.jobs.status(path.removeprefix("/api/jobs/"))
                )
            except KeyError:
                self.reply(
                    404,
                    {
                        "error": (
                            "This analysis is no longer available. Start a new one."
                        )
                    },
                )
            return

        if path.startswith("/api/deep-dives/"):
            if self.require_auth() is None:
                return
            identifier = path.removeprefix("/api/deep-dives/")
            if "/" in identifier:
                self.reply(404, {"error": "Not found."})
                return
            try:
                self.reply(200, self.server.deep_dives.status(identifier))
            except KeyError:
                self.reply(404, {"error": "This Deep Dive is no longer available. Start a new one."})
            return

        if path.startswith("/api/training/"):
            if self.require_auth() is None:
                return
            identifier = path.removeprefix("/api/training/")
            if "/" in identifier:
                self.reply(404, {"error": "Not found."})
                return
            try:
                self.reply(200, self.server.training.status(identifier))
            except KeyError:
                self.reply(404, {"error": "Training session not found."})
            return

        if path in ASSETS and ASSETS[path].is_file():
            mime = mimetypes.guess_type(ASSETS[path].name)[0] or "text/plain"
            self.reply(
                200, ASSETS[path].read_bytes(), mime + "; charset=utf-8"
            )
            return

        self.reply(404, {"error": "Not found."})

    def do_POST(self):
        if not self.allowed(mutation=True):
            return
        path = urlsplit(self.path).path
        payload = self.read_json()
        if payload is None:
            return

        try:
            if path == "/api/auth/login":
                creating = bool(payload.get("create", False))
                if set(payload) - {"username", "password", "create"}:
                    raise ValueError("Unexpected sign-in fields.")
                username = authenticate(
                    payload.get("username", ""),
                    payload.get("password", ""),
                    create=creating,
                )
                self.server.jobs.reset()
                self.server.deep_dives.reset()
                self.server.training.reset()
                with self.server.session_lock:
                    self.server.username = username
                self.reply(200, self.config_payload())
                return

            if path == "/api/auth/logout":
                username = self.require_auth()
                if username is None:
                    return
                self.server.jobs.reset()
                self.server.deep_dives.reset()
                self.server.training.reset()
                with self.server.session_lock:
                    self.server.username = None
                self.reply(200, self.config_payload())
                return

            username = self.require_auth()
            if username is None:
                return

            if path == "/api/jobs":
                request = validate_request(payload, username)
                try:
                    job = self.server.jobs.start(request, username=username)
                except ValueError as exc:
                    self.reply(409, {"error": str(exc)})
                    return
                self.reply(202, job)
                return

            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                identifier = path[
                    len("/api/jobs/") : -len("/cancel")
                ]
                self.reply(200, self.server.jobs.cancel(identifier))
                return

            if path == "/api/deep-dives":
                request = validate_request(payload, username)
                if request.get("radius_km") is None:
                    request["radius_km"] = 10
                try:
                    job = self.server.deep_dives.start(request, username=username)
                except ValueError as exc:
                    self.reply(409, {"error": str(exc)})
                    return
                self.reply(202, job)
                return

            if path.startswith("/api/deep-dives/") and path.endswith("/cancel"):
                identifier = path[
                    len("/api/deep-dives/") : -len("/cancel")
                ]
                self.reply(200, self.server.deep_dives.cancel(identifier))
                return

            if path == "/api/models/enable":
                if set(payload) != {"id"} or not isinstance(payload["id"], str):
                    raise ValueError("Choose a model to enable.")
                enable_model(payload["id"], username=username)
                self.reply(200, {"ok": True})
                return

            if path == "/api/models/delete":
                if set(payload) != {"id"} or not isinstance(payload["id"], str):
                    raise ValueError("Choose a custom model to delete.")
                delete_model(payload["id"], username=username)
                self.reply(200, {"ok": True})
                return

            if path == "/api/species/suggestions":
                if set(payload) != {"region", "query"}:
                    raise ValueError("Choose a state and enter a species search.")
                region = get_region(payload["region"])
                query = payload["query"]
                if not isinstance(query, str) or not query.strip() or len(query.strip()) > 100:
                    raise ValueError("Enter a species search up to 100 characters.")
                suggestions = species_suggestions(query.strip(), region.name, limit=3)
                self.reply(
                    200,
                    {
                        "region": region.code,
                        "region_name": region.name,
                        "suggestions": suggestions,
                    },
                )
                return

            if path == "/api/training/start":
                if set(payload) != {"region", "query"}:
                    raise ValueError("Choose a state and enter a species name.")
                job = self.server.training.start(
                    payload["region"], username, payload["query"]
                )
                self.reply(202, job)
                return

            if path.startswith("/api/training/") and path.endswith("/action"):
                identifier = path[
                    len("/api/training/") : -len("/action")
                ]
                action = payload.get("action")
                if set(payload) != {"action"} or action not in {
                    "prepare", "train", "initialize"
                }:
                    raise ValueError("Choose a valid training action.")
                self.reply(
                    202, self.server.training.action(identifier, action)
                )
                return

            if path.startswith("/api/training/") and path.endswith("/cancel"):
                identifier = path[
                    len("/api/training/") : -len("/cancel")
                ]
                self.reply(200, self.server.training.cancel(identifier))
                return

            self.reply(404, {"error": "Not found."})
        except KeyError:
            self.reply(404, {"error": "Requested item was not found."})
        except (ValueError, TypeError, OverflowError, OSError) as exc:
            self.reply(422, {"error": str(exc)})


def create_server(port=8765):
    if not 0 <= port <= 65535:
        raise ValueError("Port must be between 0 and 65535.")
    server = LocalServer(("127.0.0.1", port), Handler)
    server.token = secrets.token_urlsafe(32)
    server.jobs = JobManager()
    server.deep_dives = JobManager(worker_mode="deep-dive")
    server.training = TrainingManager()
    server.username = None
    server.session_lock = threading.Lock()
    server.origin = f"http://127.0.0.1:{server.server_port}"
    return server


def serve(port=8765, open_browser=True):
    try:
        server = create_server(port)
    except (OSError, ValueError) as exc:
        raise SystemExit(
            f"Cannot start Wild-Locate: {exc}. Try --port 8766."
        ) from exc
    print(
        f"Wild-Locate is running at {server.origin}\nPress Ctrl+C to stop.",
        flush=True,
    )
    if open_browser:
        webbrowser.open(server.origin)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
