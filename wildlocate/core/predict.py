#!/usr/bin/env python3
import json
import logging
import math
from threading import Lock

import joblib
import numpy as np
import pandas as pd
from pyproj import Geod

from wildlocate.core.environment import extract_features
from wildlocate.core.registry import available_species, resolve_model
from wildlocate.core.regional import get_region

def format_model_name(model_name):
    if model_name == "RandomForest":
        return "Random Forest"
    return model_name


def load_model_and_metadata(species, record=None):
    record = record or resolve_model(species)
    model_path = record.model_path
    metrics_path = record.metrics_path

    if not model_path.exists() or not metrics_path.exists():
        raise FileNotFoundError(
            f"No trained model found for {species}.\n"
            f"Expected at: {model_path}"
        )

    model = joblib.load(model_path)
    metrics = json.loads(metrics_path.read_text())
    return model, metrics


def validate_lat_lon(latitude, longitude):
    if latitude is None or longitude is None:
        raise ValueError("Latitude and longitude are required.")
    if not (-90 <= latitude <= 90):
        raise ValueError(f"Invalid latitude {latitude}. Latitude must be between -90 and 90.")
    if not (-180 <= longitude <= 180):
        raise ValueError(f"Invalid longitude {longitude}. Longitude must be between -180 and 180.")


def build_prediction_frame(features, predictor_names):
    missing = [name for name in predictor_names if name not in features]
    if missing:
        raise ValueError(
            "Missing required predictors from environmental feature extraction: "
            + ", ".join(missing)
        )

    missing_values = [name for name in predictor_names if pd.isna(features[name])]
    if missing_values:
        raise ValueError(
            "Environmental feature extraction returned missing values for required predictors: "
            + ", ".join(missing_values)
        )

    return pd.DataFrame([[features[name] for name in predictor_names]], columns=predictor_names)


def load_comparison_scores(model, predictor_names, species, record=None):
    record = record or resolve_model(species)
    comparison_file = record.features_path

    if not comparison_file.exists():
        raise FileNotFoundError(
            f"Could not find comparison feature dataset for {species} at {comparison_file}."
        )

    comparison_df = pd.read_csv(comparison_file)
    if predictor_names[0] not in comparison_df.columns:
        raise ValueError(
            f"Comparison dataset for {species} does not contain the expected predictor columns."
        )

    compare_X = comparison_df[predictor_names]
    scores = model.predict_proba(compare_X)[:, 1]
    return np.asarray(scores, dtype=float), comparison_df


def percentile_of_score(score, comparison_scores):
    if comparison_scores.size == 0:
        raise ValueError("Cannot compute percentile because the comparison dataset is empty.")

    score_array = np.asarray(comparison_scores, dtype=float)
    below = float(np.mean(score_array < score))
    percentile = int(round(100 * below))
    return max(0, min(100, percentile))


def category_for_percentile(percentile):
    if percentile < 20:
        return "Very Low"
    if percentile < 40:
        return "Low"
    if percentile < 60:
        return "Moderate"
    if percentile < 80:
        return "High"
    return "Very High"


def predict_species(species, latitude, longitude, region="MA", *, username=None):
    region = get_region(region).code
    species = species.strip()
    if not species:
        raise ValueError("Species name cannot be empty.")

    validate_lat_lon(latitude, longitude)

    record = resolve_model(species, region, username=username)
    species = record.species
    model, metrics = load_model_and_metadata(species, record)

    predictor_names = metrics.get("predictor_names")
    if not predictor_names:
        raise RuntimeError(
            f"Saved model metadata for {species} does not include predictor names. "
            "Please restore the model metadata."
        )

    if region == "MA":
        extracted_features = extract_features(latitude, longitude)
    else:
        from wildlocate.core.regional import SCHEMA, extract_regional_features
        if metrics.get("feature_schema") != SCHEMA or metrics.get("region") != region:
            raise ValueError("This model is not compatible with the selected region.")
        extracted_features = extract_regional_features(latitude, longitude, region)

    prediction_frame = build_prediction_frame(extracted_features, predictor_names)

    if not hasattr(model, "predict_proba"):
        raise RuntimeError(
            f"Saved model for {species} does not support predict_proba, so a relative suitability score cannot be computed."
        )

    predicted_score = float(model.predict_proba(prediction_frame)[0, 1])
    if not np.isfinite(predicted_score):
        raise RuntimeError("Prediction failed: the computed suitability score is not finite.")

    comparison_scores, comparison = load_comparison_scores(model, predictor_names, species, record)
    percentile = percentile_of_score(predicted_score, comparison_scores)
    category = category_for_percentile(percentile)

    try:
        insights = habitat_insights(model, prediction_frame, comparison, comparison_scores, predicted_score)
        insights["species"] = species
        insights["top_features"] = metrics.get("selected_model_top_features", [])
        insights["feature_values"] = {name: float(extracted_features[name]) for name in predictor_names}
    except Exception:
        logging.getLogger(__name__).exception("Habitat insights unavailable")
        insights = {"error": "Insights could not be calculated for this model. Your assessment is still available."}

    return {
        "region": region,
        "insights": insights,
        "species": species,
        "latitude": latitude,
        "longitude": longitude,
        "score": predicted_score,
        "percentile": percentile,
        "category": category,
        "model": format_model_name(metrics.get("selected_model", "Unknown Model")),
        "training_observations": int(metrics.get("presence_count", 0)),
        "features": {name: float(extracted_features[name]) for name in predictor_names},
    }



def habitat_insights(model, frame, comparison, comparison_scores, score):
    def evaluate(candidate):
        value = float(model.predict_proba(candidate)[0, 1])
        if not np.isfinite(value):
            raise ValueError('Non-finite scenario prediction')
        return value, int(round(100 * np.mean(comparison_scores < value)))

    influences = []
    for name in frame.columns:
        values = comparison[name].replace([np.inf, -np.inf], np.nan).dropna()
        if values.empty:
            continue
        reference = float(values.median())
        candidate = frame.copy()
        candidate.loc[:, name] = reference
        alternative, _ = evaluate(candidate)
        influences.append({'feature': name, 'current': float(frame.iloc[0][name]),
                           'reference': reference, 'effect': score - alternative})
    influences.sort(key=lambda item: abs(item['effect']), reverse=True)

    scenarios = []
    tested = 0
    baseline_percentile = int(round(100 * np.mean(comparison_scores < score)))
    # Search a small, explicit grid; retain the strongest visible gain per
    # scenario type. These are model experiments, not an intervention optimizer.
    for kind in ('forest', 'impervious'):
        best = None
        for fraction in (.1, .25, .5):
            candidate = frame.copy()
            changes = []
            for radius in ('250m', '1000m'):
                if kind == 'forest':
                    source, target = f'developed_fraction_{radius}', f'forest_fraction_{radius}'
                    if source not in frame or target not in frame:
                        continue
                    amount = min(float(frame.iloc[0][source]) * fraction,
                                 1 - float(frame.iloc[0][target]))
                    updates = {source: float(frame.iloc[0][source]) - amount,
                               target: float(frame.iloc[0][target]) + amount}
                else:
                    source = f'mean_impervious_{radius}'
                    if source not in frame:
                        continue
                    updates = {source: float(frame.iloc[0][source]) * (1 - fraction)}
                for name, value in updates.items():
                    before = float(frame.iloc[0][name])
                    if abs(value - before) > 1e-10:
                        candidate.loc[:, name] = value
                        changes.append({'feature': name, 'before': before, 'after': value})
            if not changes:
                continue
            tested += 1
            value, percentile = evaluate(candidate)
            delta = value - score
            if delta < .001 or (best is not None and delta <= best['delta'] + 1e-10):
                continue
            percent = int(fraction * 100)
            title = (f'Replace {percent}% of developed cover with forest' if kind == 'forest'
                     else f'Reduce impervious surface by {percent}%')
            best = {'title': title, 'score': value, 'delta': delta,
                    'percentile': percentile, 'changes': changes}
        if best is not None:
            scenarios.append(best)
    scenarios.sort(key=lambda item: item['delta'], reverse=True)
    return {'influences': influences, 'scenarios': scenarios,
            'baseline_score': float(score), 'baseline_percentile': baseline_percentile,
            'scenarios_tested': tested}


def build_grid(latitude, longitude, radius_km):
    if isinstance(radius_km, bool) or radius_km not in (10, 25, 50):
        raise ValueError('Choose a radius of 10, 25 or 50 km.')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (latitude, longitude)):
        raise ValueError('Enter finite latitude and longitude coordinates.')
    validate_lat_lon(latitude, longitude)
    geod = Geod(ellps='WGS84')
    spacing = radius_km * 1000 / 5
    points = []
    for north in range(-5, 6):
        for east in range(-5, 6):
            if north*north + east*east > 25:
                continue
            distance = math.hypot(east, north) * spacing
            lon, lat, _ = geod.fwd(longitude, latitude, math.degrees(math.atan2(east, north)), distance)
            points.append({'latitude': float(lat), 'longitude': float(lon)})
    return points


def predict_area(species, latitude, longitude, radius_km, region='MA', *, username=None):
    points = build_grid(latitude, longitude, radius_km)
    region = get_region(region).code
    record = resolve_model(species, region, username=username)
    model, metrics = load_model_and_metadata(record.species, record)
    predictors = metrics.get('predictor_names')
    if not predictors:
        raise RuntimeError('Saved model metadata does not include predictor names.')
    if not hasattr(model, 'predict_proba'):
        raise RuntimeError('Saved model does not support suitability scoring.')
    extractor = extract_features
    if region != 'MA':
        from wildlocate.core.regional import SCHEMA, extract_regional_features
        if metrics.get('feature_schema') != SCHEMA or metrics.get('region') != region:
            raise ValueError('This model is not compatible with the selected region.')
        extractor = lambda lat, lon: extract_regional_features(lat, lon, region)
    comparison_scores, _ = load_comparison_scores(model, predictors, record.species, record)
    for point in points:
        try:
            features = extractor(point['latitude'], point['longitude'])
            frame = build_prediction_frame(features, predictors)
            if not np.isfinite(frame.to_numpy(dtype=float)).all():
                raise ValueError('Environmental feature extraction returned missing values')
        except ValueError as exc:
            if not str(exc).startswith(('Requested coordinate cannot be evaluated', 'Environmental feature extraction returned missing values')):
                raise
            point.update(status='unavailable', reason='Outside model coverage or incomplete environmental data.')
            continue
        score = float(model.predict_proba(frame)[0, 1])
        if not math.isfinite(score):
            raise RuntimeError('Prediction returned a non-finite suitability score.')
        percentile = percentile_of_score(score, comparison_scores)
        point.update(status='ok', score=score, percentile=percentile, category=category_for_percentile(percentile))
    scored = [p for p in points if p['status'] == 'ok']
    return {
        'analysis_type': 'regional', 'species': record.species, 'region': region,
        'latitude': latitude, 'longitude': longitude, 'radius_km': radius_km,
        'grid_spacing_km': radius_km / 5, 'points': points,
        'evaluated_points': len(scored), 'unavailable_points': len(points) - len(scored),
        'mean_score': float(np.mean([p['score'] for p in scored])) if scored else None,
        'model': format_model_name(metrics.get('selected_model', 'Unknown Model')),
        'training_observations': int(metrics.get('presence_count', 0)),
    }

logger = logging.getLogger(__name__)
# The existing extractor caches open raster handles. Serialize calls without
# changing extraction or sharing those handles across concurrent predictions.
_prediction_lock = Lock()


class PredictionError(Exception):
    def __init__(self, message, code, status_code=422):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def assess_habitat(species, latitude, longitude, region="MA", *, username=None, radius_km=None):
    from wildlocate.core.regional import get_region
    try:
        selected_region = get_region(region)
    except ValueError as exc:
        raise PredictionError(str(exc), "unsupported_region") from exc
    region = selected_region.code
    species = species.strip() if isinstance(species, str) else ""
    canonical = next((name for name in available_species(region, username=username) if name.casefold() == species.casefold()), None)
    if canonical is None:
        raise PredictionError("Choose an available species, or enable a trained model in Manage species.", "unsupported_species")
    try:
        if isinstance(latitude, bool) or isinstance(longitude, bool):
            raise ValueError
        latitude, longitude = float(latitude), float(longitude)
        validate_lat_lon(latitude, longitude)
    except (ValueError, TypeError, OverflowError) as exc:
        raise PredictionError(
            "Enter a latitude from −90 to 90 and a longitude from −180 to 180.",
            "invalid_coordinates",
        ) from exc

    if radius_km is not None and (isinstance(radius_km, bool) or radius_km not in (10, 25, 50)):
        raise PredictionError("Choose a radius of 10, 25 or 50 km.", "invalid_radius")
    try:
        with _prediction_lock:
            if radius_km is not None:
                return predict_area(canonical, latitude, longitude, radius_km, region, username=username)
            result = predict_species(canonical, latitude, longitude, region, username=username)
        if not all(math.isfinite(value) for value in result["features"].values()):
            raise ValueError("Environmental feature extraction returned missing values")
        return result
    except FileNotFoundError as exc:
        logger.exception("Required prediction files are unavailable")
        if str(exc).startswith("No trained model found"):
            raise PredictionError(
                f"The saved model for {canonical} is unavailable. Restore its model and metadata files.",
                "model_unavailable", 503,
            ) from exc
        raise PredictionError(
            "Required environmental or comparison data is missing. Run 'wildlocate init' to download datasets.",
            "data_unavailable", 503,
        ) from exc
    except ValueError as exc:
        logger.exception("Prediction could not evaluate the location")
        if str(exc).startswith(("Requested coordinate cannot be evaluated", "Environmental feature extraction returned missing values")):
            raise PredictionError(
                f"This location is outside the available {selected_region.name} coverage or has incomplete data. Try another location in {selected_region.name}.",
                "location_unavailable",
            ) from exc
        raise PredictionError(
            "The location could not be analyzed with the saved model and environmental data. Check the local data files and try again.",
            "prediction_failed", 500,
        ) from exc
    except Exception as exc:
        logger.exception("Habitat prediction failed")
        raise PredictionError(
            "The habitat analysis could not be completed. Check that the model and environmental files are available, then try again.",
            "prediction_failed", 500,
        ) from exc
