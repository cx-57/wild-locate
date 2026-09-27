"""Feature-dataset construction, model training, and training sessions."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import uuid

import joblib
import numpy as np
import pandas as pd
from pyproj import Transformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from wildlocate.core.environment import extract_features
from wildlocate.core.observations import species_slug
from wildlocate.core.registry import (
    ModelRecord, atomic_json, cleanup_job, complete, custom_root, job_path,
    normalize_username,
)
DEFAULT_INPUT_DIR = Path("data/processed/samples")
DEFAULT_OUTPUT_DIR = Path("data/processed/features/species")
DEFAULT_FAILURE_THRESHOLD = 0.05


def validate_training_points(df):
    required_columns = {"latitude", "longitude", "presence"}
    missing = sorted(required_columns.difference(df.columns))
    if missing:
        raise ValueError(
            f"Training points file is missing required columns: {missing}"
        )

    df["presence"] = pd.to_numeric(df["presence"], errors="coerce")
    if df["presence"].isna().any():
        raise ValueError("Training points file contains non-numeric or missing presence values.")

    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")

    if df[["latitude", "longitude"]].isna().any().any():
        raise ValueError("Training points file contains missing latitude/longitude values.")


def build_species_dataset(
    species_name,
    training_points_file=None,
    output_file=None,
    progress=None,
    region="MA",
):
    slug = species_slug(species_name)
    training_path = Path(training_points_file) if training_points_file else DEFAULT_INPUT_DIR / f"{slug}_training_points.csv"

    if not training_path.exists():
        raise FileNotFoundError(
            f"Could not find training points at {training_path}. Run the Phase 4 background step first."
        )

    training_df = pd.read_csv(training_path)
    validate_training_points(training_df)
    if region != "MA":
        from wildlocate.core.regional import prefetch_training_tiles
        prefetch_training_tiles(training_df, region, progress)

    output_path = Path(output_file) if output_file else DEFAULT_OUTPUT_DIR / f"{slug}_features.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    successful_rows = []
    failed_rows = []

    total_rows = len(training_df)
    presence_count = int((training_df["presence"] == 1).sum())
    background_count = int((training_df["presence"] == 0).sum())

    feature_columns = None

    for row_idx, row in training_df.iterrows():
        if progress and (row_idx % 25 == 0 or row_idx == total_rows - 1):
            progress(f"Extracting environmental features: {row_idx + 1:,} of {total_rows:,} locations…")
        latitude = float(row["latitude"])
        longitude = float(row["longitude"])
        presence = int(row["presence"])

        try:
            if region == "MA":
                features = extract_features(latitude, longitude)
            else:
                from wildlocate.core.regional import extract_regional_features
                features = extract_regional_features(latitude, longitude, region, progress)
        except Exception as exc:
            failed_rows.append(
                {
                    "row_index": int(row_idx),
                    "latitude": latitude,
                    "longitude": longitude,
                    "presence": presence,
                    "error": str(exc),
                }
            )
            continue

        if feature_columns is None:
            feature_columns = list(features.keys())

        combined_row = {
            "latitude": latitude,
            "longitude": longitude,
            "presence": presence,
        }
        combined_row.update(features)
        successful_rows.append(combined_row)

    if not successful_rows:
        raise RuntimeError("No rows were successfully processed; feature dataset could not be built.")

    output_df = pd.DataFrame(successful_rows)

    output_columns = ["latitude", "longitude", "presence", *feature_columns]
    output_df = output_df.reindex(columns=output_columns)

    output_df.to_csv(output_path, index=False)

    failed_presence_rows = sum(1 for row in failed_rows if int(row["presence"]) == 1)
    failed_background_rows = sum(1 for row in failed_rows if int(row["presence"]) == 0)

    overall_failure_rate = len(failed_rows) / total_rows
    presence_failure_rate = failed_presence_rows / presence_count if presence_count else 0.0
    background_failure_rate = failed_background_rows / background_count if background_count else 0.0

    class_failure_disproportionate = False
    if presence_count and background_count:
        max_rate = max(presence_failure_rate, background_failure_rate)
        min_rate = min(presence_failure_rate, background_failure_rate)
        class_failure_disproportionate = max_rate > 0.0 and max_rate >= (2.0 * min_rate) and max_rate > DEFAULT_FAILURE_THRESHOLD

    if (
        overall_failure_rate > DEFAULT_FAILURE_THRESHOLD
        or presence_failure_rate > DEFAULT_FAILURE_THRESHOLD
        or background_failure_rate > DEFAULT_FAILURE_THRESHOLD
        or class_failure_disproportionate
    ):
        raise RuntimeError(
            "Feature build failed because failure thresholds were exceeded: "
            f"overall={overall_failure_rate:.4f}, presence={presence_failure_rate:.4f}, "
            f"background={background_failure_rate:.4f}, class_disproportionate={class_failure_disproportionate}."
        )

    print(f"Saved feature dataset to: {output_path}")
    print(f"Input rows: {total_rows}")
    print(f"Output rows: {len(output_df)}")
    print(f"Failed rows: {len(failed_rows)}")

    if failed_rows:
        print("Sample failures:")
        for failure in failed_rows[:10]:
            print(
                f"  row_index={failure['row_index']} presence={failure['presence']} "
                f"lat={failure['latitude']} lon={failure['longitude']} error={failure['error']}"
            )

    return output_df, pd.DataFrame(failed_rows)




OUTPUT_DIR = Path("data/processed/models")
TARGET_COLUMN = "presence"
RANDOM_STATE = 42
N_FOLDS = 5
DEFAULT_BLOCK_SIZE_M = 10000
EXCLUDED_COLUMNS = {
    "latitude",
    "longitude",
    "presence",
    "species",
    "species_name",
    "observation_id",
    "taxon_id",
    "species_slug",
}
MODEL_COMPLEXITY = {"LogisticRegression": 0, "RandomForest": 1, "XGBoost": 2}


def load_dataset(dataset_file):
    df = pd.read_csv(dataset_file)
    if TARGET_COLUMN not in df.columns:
        raise ValueError(f"Target column '{TARGET_COLUMN}' not found in {dataset_file}")
    return df


def infer_feature_columns(df):
    feature_columns = [
        column
        for column in df.columns
        if column not in EXCLUDED_COLUMNS and column != TARGET_COLUMN
    ]

    if not feature_columns:
        raise ValueError("No predictor columns were found in the feature dataset.")

    leakage_columns = [
        column
        for column in feature_columns
        if column.lower() in {"latitude", "longitude", "presence", "species", "species_name", "observation_id", "taxon_id"}
    ]
    if leakage_columns:
        raise ValueError(
            "Feature leakage detected: the following columns were incorrectly included as predictors: "
            + ", ".join(leakage_columns)
        )

    return feature_columns


def build_spatial_groups(df, block_size_m):
    if block_size_m <= 0:
        raise ValueError("block_size_m must be positive.")

    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    lon_values = df["longitude"].to_numpy(dtype=float)
    lat_values = df["latitude"].to_numpy(dtype=float)
    x_m, y_m = transformer.transform(lon_values, lat_values)

    block_x = np.floor(x_m / block_size_m).astype(int)
    block_y = np.floor(y_m / block_size_m).astype(int)
    groups = np.char.add(block_x.astype(str), np.char.add("_", block_y.astype(str)))
    return groups


def build_estimator(model_name):
    if model_name == "LogisticRegression":
        return Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        max_iter=5000,
                        class_weight="balanced",
                        random_state=RANDOM_STATE,
                        solver="lbfgs",
                    ),
                ),
            ]
        )

    if model_name == "RandomForest":
        return Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
                (
                    "model",
                    RandomForestClassifier(
                        n_estimators=300,
                        max_depth=None,
                        min_samples_leaf=2,
                        class_weight="balanced",
                        random_state=RANDOM_STATE,
                        n_jobs=-1,
                    ),
                ),
            ]
        )

    if model_name == "XGBoost":
        try:
            from xgboost import XGBClassifier
        except Exception as exc:
            raise RuntimeError("XGBoost is not available.") from exc

        return Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
                (
                    "model",
                    XGBClassifier(
                        n_estimators=300,
                        max_depth=4,
                        learning_rate=0.05,
                        subsample=0.8,
                        colsample_bytree=0.8,
                        reg_alpha=0.5,
                        reg_lambda=1.0,
                        min_child_weight=2,
                        objective="binary:logistic",
                        eval_metric="logloss",
                        random_state=RANDOM_STATE,
                        n_jobs=-1,
                        use_label_encoder=False,
                    ),
                ),
            ]
        )

    raise ValueError(f"Unsupported model type: {model_name}")


def build_models():
    models = [
        {"name": "LogisticRegression", "estimator": None},
        {"name": "RandomForest", "estimator": None},
    ]

    try:
        from xgboost import XGBClassifier  # noqa: F401
        models.append({"name": "XGBoost", "estimator": None})
    except Exception:
        print("XGBoost not available; skipping XGBoost model.")

    return models


def select_spatial_splits(df, feature_columns, y):
    candidate_block_sizes = [10000, 15000, 20000, 25000, 30000, 40000, 50000]
    last_error = None

    X = df[feature_columns]

    for block_size_m in candidate_block_sizes:
        groups = build_spatial_groups(df, block_size_m=block_size_m)
        splitter = StratifiedGroupKFold(n_splits=N_FOLDS, shuffle=True, random_state=RANDOM_STATE)

        try:
            splits = list(splitter.split(X, y, groups))
        except Exception as exc:
            last_error = exc
            continue

        fold_validity_errors = []
        for fold_index, (train_idx, val_idx) in enumerate(splits, start=1):
            y_train = y.iloc[train_idx]
            y_val = y.iloc[val_idx]

            train_classes = np.unique(y_train.to_numpy(dtype=int))
            val_classes = np.unique(y_val.to_numpy(dtype=int))

            if len(train_classes) < 2:
                fold_validity_errors.append(
                    f"Fold {fold_index} training split contains only one class; block size {block_size_m}m is too coarse."
                )
                continue
            if len(val_classes) < 2:
                fold_validity_errors.append(
                    f"Fold {fold_index} validation split contains only one class; block size {block_size_m}m is too coarse."
                )
                continue

        if fold_validity_errors:
            last_error = RuntimeError("; ".join(fold_validity_errors))
            continue

        notes = [
            "Used spatial 10 km x 10 km blocks for cross-validation." if block_size_m == DEFAULT_BLOCK_SIZE_M else f"Used spatial {block_size_m}m blocks because the default 10 km grid produced invalid folds."
        ]
        return groups, splits, block_size_m, notes

    if last_error is None:
        raise RuntimeError("Unable to build any valid spatial cross-validation splits.")

    raise RuntimeError(
        "Unable to build valid spatial cross-validation splits with the requested 10 km blocks. "
        f"Last error: {last_error}"
    )


def evaluate_models(df, feature_columns, splits, progress=None):
    X = df[feature_columns]
    y = df[TARGET_COLUMN].astype(int)

    model_specs = build_models()
    model_results = []

    for model_info in model_specs:
        model_name = model_info["name"]
        fold_metrics = []
        fold_roc_values = []
        fold_pr_values = []

        for fold_index, (train_idx, val_idx) in enumerate(splits, start=1):
            if progress:
                progress(f"Evaluating {model_name}: spatial fold {fold_index} of {len(splits)}…")
            X_train = X.iloc[train_idx].copy()
            y_train = y.iloc[train_idx].copy().astype(int)
            X_val = X.iloc[val_idx].copy()
            y_val = y.iloc[val_idx].copy().astype(int)

            if y_train.nunique() < 2 or y_val.nunique() < 2:
                raise RuntimeError(
                    f"Model {model_name} fold {fold_index} does not contain both classes after spatial splitting."
                )

            estimator = build_estimator(model_name)
            estimator.fit(X_train, y_train)
            val_proba = estimator.predict_proba(X_val)[:, 1]

            roc_auc = float(roc_auc_score(y_val, val_proba))
            pr_auc = float(average_precision_score(y_val, val_proba))

            if not np.isfinite(roc_auc) or not np.isfinite(pr_auc):
                raise RuntimeError(
                    f"Non-finite metric encountered for {model_name} in fold {fold_index}."
                )

            fold_roc_values.append(roc_auc)
            fold_pr_values.append(pr_auc)
            fold_metrics.append(
                {
                    "fold": fold_index,
                    "roc_auc": roc_auc,
                    "pr_auc": pr_auc,
                    "train_presence": int(y_train.sum()),
                    "train_background": int((1 - y_train).sum()),
                    "val_presence": int(y_val.sum()),
                    "val_background": int((1 - y_val).sum()),
                }
            )

        model_results.append(
            {
                "model": model_name,
                "fold_metrics": fold_metrics,
                "mean_roc_auc": float(np.mean(fold_roc_values)),
                "std_roc_auc": float(np.std(fold_roc_values, ddof=0)),
                "mean_pr_auc": float(np.mean(fold_pr_values)),
                "std_pr_auc": float(np.std(fold_pr_values, ddof=0)),
                "roc_auc_folds": [float(value) for value in fold_roc_values],
                "pr_auc_folds": [float(value) for value in fold_pr_values],
            }
        )

    return model_results, {"models": model_results}


def pick_best_model(model_results):
    sorted_results = sorted(
        model_results,
        key=lambda item: (
            -item["mean_pr_auc"],
            -item["mean_roc_auc"],
            MODEL_COMPLEXITY.get(item["model"], 99),
        ),
    )
    return sorted_results[0]


def top_features_for_model(model_name, fitted_pipeline, feature_columns, top_n=5):
    if model_name == "LogisticRegression":
        coefficients = fitted_pipeline.named_steps["model"].coef_[0]
        ranked = sorted(
            zip(feature_columns, np.abs(coefficients), coefficients),
            key=lambda item: item[1],
            reverse=True,
        )
        return [
            {
                "feature": feature,
                "absolute_coefficient": float(abs_coefficient),
                "coefficient": float(coefficient),
            }
            for feature, abs_coefficient, coefficient in ranked[:top_n]
        ]

    if model_name in {"RandomForest", "XGBoost"}:
        importances = fitted_pipeline.named_steps["model"].feature_importances_
        ranked = sorted(
            zip(feature_columns, importances),
            key=lambda item: item[1],
            reverse=True,
        )
        return [
            {
                "feature": feature,
                "importance": float(importance),
            }
            for feature, importance in ranked[:top_n]
        ]

    raise ValueError(f"Unsupported model for feature interpretation: {model_name}")


def save_metrics_json(metrics_path, payload):
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(payload, indent=2))


def train_species(species, dataset_file=None, output_dir=OUTPUT_DIR, progress=None):
    species = species.strip()
    slug = species_slug(species)
    dataset_file = Path(dataset_file) if dataset_file is not None else Path(f"data/processed/features/species/{slug}_features.csv")
    output_dir = Path(output_dir)

    if not dataset_file.exists():
        raise FileNotFoundError(f"Could not find species feature dataset at {dataset_file}.")

    print(f"Loading species feature dataset for {species} from {dataset_file}...")
    df = load_dataset(dataset_file)

    feature_columns = infer_feature_columns(df)
    if "latitude" not in df.columns or "longitude" not in df.columns:
        raise ValueError("Latitude and longitude must be present in the feature dataset.")

    missing_values = {
        column: int(df[column].isna().sum())
        for column in feature_columns
        if df[column].isna().any()
    }
    if missing_values:
        print("Detected missing values in predictor columns; median imputation will be used inside each training fold.")
        print(json.dumps(missing_values, indent=2))

    print("\nPreparing spatial cross-validation...")
    y = df[TARGET_COLUMN].astype(int)
    groups, splits, block_size_m, split_notes = select_spatial_splits(df, feature_columns, y)

    print(f"Using spatial block size: {block_size_m} meters")
    for note in split_notes:
        print(note)

    print(f"Spatial folds generated: {len(splits)}")
    for fold_index, (_, val_idx) in enumerate(splits, start=1):
        y_val = y.iloc[val_idx]
        print(f"Fold {fold_index}: presence={int(y_val.sum())}, background={int((1 - y_val).sum())}")

    if any((y.iloc[val_idx].sum() == 0) or ((1 - y.iloc[val_idx]).sum() == 0) for _, val_idx in splits):
        raise RuntimeError("At least one spatial fold is missing one of the target classes.")

    print("\nRunning spatial cross-validation for each candidate model...")
    model_results, _ = evaluate_models(df, feature_columns, splits, progress=progress)

    best_result = pick_best_model(model_results)

    print("\nModel comparison summary:")
    print(
        pd.DataFrame(
            [
                {
                    "Model": item["model"],
                    "Mean ROC-AUC": item["mean_roc_auc"],
                    "Mean PR-AUC": item["mean_pr_auc"],
                }
                for item in model_results
            ]
        )
        .sort_values(["Mean PR-AUC", "Mean ROC-AUC"], ascending=False)
        .to_string(index=False)
    )

    print(f"\nSelected model: {best_result['model']}")

    X_full = df[feature_columns]
    y_full = df[TARGET_COLUMN].astype(int)
    selected_pipeline = build_estimator(best_result["model"])
    if progress:
        progress(f"Fitting the selected {best_result['model']} model on all training locations…")
    selected_pipeline.fit(X_full, y_full)

    selected_features = top_features_for_model(best_result["model"], selected_pipeline, feature_columns)

    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / f"{slug}.joblib"
    metrics_path = output_dir / f"{slug}_metrics.json"

    joblib.dump(selected_pipeline, model_path)

    metrics_payload = {
        "species": species,
        "species_slug": slug,
        "total_rows": int(len(df)),
        "presence_count": int(y_full.sum()),
        "background_count": int((1 - y_full).sum()),
        "predictor_names": feature_columns,
        "spatial_block_size_m": int(block_size_m),
        "number_of_folds": N_FOLDS,
        "random_state": RANDOM_STATE,
        "selected_model": best_result["model"],
        "candidate_models": [item["model"] for item in model_results],
        "leakage_check": {
            "excluded_columns": sorted(EXCLUDED_COLUMNS),
            "evidence": {
                "latitude_not_predictor": "latitude" not in feature_columns,
                "longitude_not_predictor": "longitude" not in feature_columns,
                "presence_not_predictor": TARGET_COLUMN not in feature_columns,
            },
        },
        "split_notes": split_notes,
        "metrics_by_model": [],
        "selected_model_top_features": selected_features,
    }

    for item in model_results:
        metrics_payload["metrics_by_model"].append(
            {
                "model": item["model"],
                "mean_roc_auc": item["mean_roc_auc"],
                "std_roc_auc": item["std_roc_auc"],
                "mean_pr_auc": item["mean_pr_auc"],
                "std_pr_auc": item["std_pr_auc"],
                "roc_auc_folds": item["roc_auc_folds"],
                "pr_auc_folds": item["pr_auc_folds"],
                "fold_metrics": item["fold_metrics"],
            }
        )

    save_metrics_json(metrics_path, metrics_payload)

    print(f"\nSaved trained model to: {model_path}")
    print(f"Saved metrics metadata to: {metrics_path}")
    return model_path, metrics_path, metrics_payload

# Training session orchestration



MIN_OBSERVATIONS = 25
SUPPORTED_TARGET_GROUPS = {"Mammalia": "mammal", "Reptilia": "reptile"}


class TrainingSession:
    def __init__(self, job_id, progress=lambda message: None, region="MA", max_observations=5000, *, username=None):
        from wildlocate.core.regional import get_region
        self.region = get_region(region)
        self.max_observations = max_observations
        self.job_id = job_id
        self.username = normalize_username(username)
        self.workspace = job_path(job_id, username=self.username)
        self.progress = progress
        self.taxon = None
        self.prepared = None

    def initialize_environment(self):
        if self.region.code != "MA":
            from wildlocate.core.regional import initialize
            initialize(self.region, self.progress)
            return {}
        from wildlocate.cli import cmd_init
        from wildlocate.core.environment import get_user_data_dir
        destination = get_user_data_dir() / "raw"
        staging = self.workspace / "environment"
        previous = os.environ.get("WILDLOCATE_DATA_DIR")
        self.progress("Downloading environmental datasets. This may take several minutes…")
        try:
            os.environ["WILDLOCATE_DATA_DIR"] = str(staging)
            cmd_init(None)
        finally:
            if previous is None:
                os.environ.pop("WILDLOCATE_DATA_DIR", None)
            else:
                os.environ["WILDLOCATE_DATA_DIR"] = previous
        # Publish only finished downloads; cancellation leaves installed data intact.
        for source in (staging / "raw").rglob("*"):
            if source.is_file():
                target = destination / source.relative_to(staging / "raw")
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
        return {}

    def resolve(self, query):
        from wildlocate.core.observations import resolve_species
        self.taxon = None
        self.prepared = None
        self.progress("Finding the species on iNaturalist…")
        taxon = resolve_species(query)
        if taxon.get("rank") != "species":
            raise ValueError("Choose a species-level taxon.")
        if taxon.get("iconic_taxon_name") not in SUPPORTED_TARGET_GROUPS:
            raise ValueError("Choose a mammal or reptile species.")
        if len(taxon["common_name"]) > 100:
            raise ValueError("This species name is too long to store.")
        self.taxon = taxon
        return taxon

    def prepare(self):
        from wildlocate.core.environment import DatasetPaths
        from wildlocate.core.observations import (
            clean_species_observations, download_species_observations, save_cleaned_observations,
        )
        self.prepared = None
        if self.taxon is None:
            raise ValueError("Find and confirm a mammal or reptile species first.")
        self.progress(f"Checking the {self.region.name} environmental datasets…")
        if self.region.code == "MA":
            DatasetPaths().validate()
        else:
            from wildlocate.core.regional import initialize
            initialize(self.region, self.progress)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.progress(f"Downloading research-grade {self.region.name} observations…")
        raw = download_species_observations(
            self.taxon["common_name"], place_name=self.region.name, taxon_id=self.taxon["taxon_id"], progress=self.progress,
            max_observations=self.max_observations,
        )
        cleaned = clean_species_observations(raw)
        if len(cleaned) < MIN_OBSERVATIONS:
            raise ValueError(
                f"Only {len(cleaned):,} usable observations remain out of {len(raw):,}. "
                f"At least {MIN_OBSERVATIONS} are required to attempt spatial validation. "
                f"Try another {self.region.name} mammal or reptile; obscured, duplicate and imprecise locations are excluded."
            )
        save_cleaned_observations(cleaned, self.taxon["common_name"], self.workspace / "samples")
        self.prepared = {"species": self.taxon["common_name"], "raw_count": len(raw), "cleaned_count": len(cleaned), "download_limit": self.max_observations}
        return self.prepared

    def train(self):
        from wildlocate.core.observations import generate_background
        from wildlocate.core.observations import species_slug
        if self.prepared is None or self.taxon is None:
            raise ValueError("Check species data before starting training.")
        name = self.taxon["common_name"]
        samples = self.workspace / "samples"
        target_group = self.taxon["iconic_taxon_name"]
        group_label = SUPPORTED_TARGET_GROUPS[target_group]
        pool = self.workspace / f"{group_label}_pool.csv"
        from wildlocate.core.regional import region_root
        region_slug = self.region.name.lower().replace(" ", "_")
        cached_pool = (
            region_root(self.region)
            / "training-cache"
            / f"{region_slug}_{group_label}_pool.csv"
        )
        if cached_pool.is_file():
            shutil.copyfile(cached_pool, pool)
        self.progress(f"Preparing {group_label} background locations across {self.region.name}…")
        generate_background(
            name,
            samples_dir=samples,
            pool_file=pool,
            taxon_id=self.taxon["taxon_id"],
            progress=self.progress,
            place_name=self.region.name,
            target_group=target_group,
        )
        if pool.is_file():
            cached_pool.parent.mkdir(parents=True, exist_ok=True)
            temporary = cached_pool.with_suffix(f".{self.job_id}.tmp")
            shutil.copyfile(pool, temporary)
            os.replace(temporary, cached_pool)
        self.progress("Extracting environmental features…")
        features = self.workspace / "features.csv"
        build_species_dataset(name, samples / f"{species_slug(name)}_training_points.csv",
                              features, progress=self.progress, region=self.region.code)
        self.progress("Evaluating candidate models using five spatial folds…")
        model_path, metrics_path, metrics = train_species(
            name, features, self.workspace / "fitted", progress=self.progress,
        )
        if self.region.code != "MA":
            metrics["region"] = self.region.code
            metrics["feature_schema"] = "regional-raster-v1"
            atomic_json(metrics_path, metrics)
        self.progress("Checking the completed model and saving it for review…")
        # Validate the serialized artifact and all comparison predictions before publishing.
        import joblib
        import numpy as np
        import pandas as pd
        model = joblib.load(model_path)
        frame = pd.read_csv(features)[metrics["predictor_names"]]
        scores = model.predict_proba(frame)[:, 1]
        if not len(scores) or not np.isfinite(scores).all():
            raise ValueError("The trained model produced invalid comparison scores.")
        review = self.workspace / "review"
        review.mkdir(exist_ok=True)
        shutil.copyfile(model_path, review / "model.joblib")
        shutil.copyfile(metrics_path, review / "metrics.json")
        shutil.copyfile(features, review / "features.csv")
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        atomic_json(review / "manifest.json", {
            "version": 1, "species": name, "taxon": self.taxon,
            "created_at": created_at, "region": self.region.code, **self.prepared,
            "owner": self.username,
        })
        record = ModelRecord(self.job_id, name, review / "model.joblib", review / "metrics.json", review / "features.csv", True, region=self.region.code)
        if not complete(record):
            raise ValueError("The model files are incomplete. Training was not published.")
        custom_root(self.username).mkdir(parents=True, exist_ok=True)
        # The destination is invisible to discovery until every artifact is ready.
        destination = custom_root(self.username) / self.job_id
        if destination.exists():
            raise ValueError("This training session has already saved a model.")
        os.replace(review, destination)
        self.prepared = None
        cleanup_job(self.job_id, username=self.username)
        return {"model_id": self.job_id, "species": name}


def new_job_id():
    return uuid.uuid4().hex
