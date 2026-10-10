"""Deeper habitat analysis for a completed Wild-Locate assessment."""

import math
import logging
from collections import defaultdict

import numpy as np
from pyproj import Geod

from wildlocate.core.environment import extract_features
from wildlocate.core.place_analysis import analyze_places
from wildlocate.core.predict import (
    build_grid,
    build_prediction_frame,
    category_for_percentile,
    format_model_name,
    habitat_insights,
    load_comparison_scores,
    load_model_and_metadata,
    model_evidence,
    percentile_of_score,
)
from wildlocate.core.registry import resolve_model
from wildlocate.core.regional import get_region

logger = logging.getLogger(__name__)
EFFECT_THRESHOLD = 1e-8
SECTOR_NAMES = ("North", "Northeast", "East", "Southeast",
                "South", "Southwest", "West", "Northwest")
GEOD = Geod(ellps="WGS84")


def _feature_domain(name):
    lowered = name.casefold()
    if "developed" in lowered or "impervious" in lowered:
        return "Development"
    if "road" in lowered:
        return "Road exposure"
    if "forest" in lowered:
        return "Forest structure"
    if "wetland" in lowered or "water" in lowered:
        return "Water & wetlands"
    if any(term in lowered for term in ("slope", "rugged", "elevation", "terrain")):
        return "Terrain"
    return "Other"


def _sector(latitude, longitude, center_latitude, center_longitude):
    north = math.radians(latitude - center_latitude) * 6371.0
    east = (
        math.radians(longitude - center_longitude)
        * 6371.0
        * math.cos(math.radians(center_latitude))
    )
    if math.hypot(north, east) < 0.05:
        return "Center"
    angle = (math.degrees(math.atan2(east, north)) + 360) % 360
    return SECTOR_NAMES[int((angle + 22.5) // 45) % 8]


def _compact_point(point):
    if not point:
        return None
    return {
        "latitude": point["latitude"],
        "longitude": point["longitude"],
        "score": point["score"],
        "percentile": point["percentile"],
        "category": point["category"],
    }


def summarize_deep_dive(points, center_latitude, center_longitude):
    """Describe evaluated samples and one-feature median comparisons separately."""
    scored = [point for point in points if point.get("status") == "ok"]
    explained = [point for point in scored
                 if point.get("insight_status") != "unavailable" and "influences" in point]
    total_points = len(points)
    evaluated_points = len(scored)
    unavailable_points = total_points - evaluated_points
    failed_explanations = evaluated_points - len(explained)
    percentiles = [point["percentile"] for point in scored]
    ranked = sorted(scored, key=lambda point: (
        -point["percentile"], -point["score"], point["latitude"], point["longitude"]
    ))
    strongest = ranked[0] if ranked else None
    high_points = sum(value >= 60 for value in percentiles)

    warnings = []
    if not scored:
        warnings.append("No samples could be evaluated in this landscape. Choose a location with available environmental data.")
    elif unavailable_points:
        warnings.append(f"{unavailable_points} samples were unavailable and are excluded from the distribution and model summaries.")
    if failed_explanations:
        warnings.append(f"Model explanations were unavailable for {failed_explanations} evaluated samples. Their suitability scores remain available.")

    categories = ("Very Low", "Low", "Moderate", "High", "Very High")
    distribution = []
    for category in categories:
        count = sum(category_for_percentile(value) == category for value in percentiles)
        distribution.append({
            "category": category,
            "count": count,
            "percentage": 100.0 * count / evaluated_points if evaluated_points else 0.0,
        })

    sector_values = defaultdict(list)
    for point in scored:
        sector_values[_sector(
            point["latitude"], point["longitude"], center_latitude, center_longitude
        )].append(point["percentile"])
    sectors = [
        {"name": name, "mean_percentile": float(np.mean(sector_values[name])),
         "points": len(sector_values[name])}
        for name in SECTOR_NAMES if sector_values[name]
    ]
    eligible_sectors = [sector for sector in sectors if sector["points"] >= 3]
    priorities = []
    for rank, point in enumerate(ranked[:3], start=1):
        _, _, distance_m = GEOD.inv(
            center_longitude, center_latitude, point["longitude"], point["latitude"]
        )
        if (point["latitude"], point["longitude"]) == (center_latitude, center_longitude):
            distance_m = 0.0
        priorities.append({
            **_compact_point(point),
            "rank": rank,
            "sector": _sector(point["latitude"], point["longitude"], center_latitude, center_longitude),
            "distance_km": float(distance_m / 1000),
        })

    effects = defaultdict(list)
    feature_values = defaultdict(lambda: {"current": [], "reference": []})
    pressure_domains = defaultdict(lambda: {"magnitude": 0.0, "points": set(), "features": set()})
    for point_index, point in enumerate(explained):
        for influence in point.get("influences", []):
            name = influence["feature"]
            effect = float(influence["effect"])
            if not math.isfinite(effect):
                continue
            effects[name].append(effect)
            for value_name in ("current", "reference"):
                value = influence.get(value_name)
                if value is not None and math.isfinite(float(value)):
                    feature_values[name][value_name].append(float(value))
            if effect < -EFFECT_THRESHOLD:
                domain = _feature_domain(name)
                pressure_domains[domain]["magnitude"] += -effect
                pressure_domains[domain]["points"].add(point_index)
                pressure_domains[domain]["features"].add(name)

    drivers = []
    for name, values in effects.items():
        if not values:
            continue
        mean_effect = float(np.mean(values))
        drivers.append({
            "feature": name,
            "mean_effect": mean_effect,
            "affected_points": sum(
                value > EFFECT_THRESHOLD if mean_effect > 0 else value < -EFFECT_THRESHOLD
                for value in values
            ),
            "points_evaluated": len(values),
            "mean_current": float(np.mean(feature_values[name]["current"]))
                            if feature_values[name]["current"] else None,
            "mean_reference": float(np.mean(feature_values[name]["reference"]))
                              if feature_values[name]["reference"] else None,
        })
        drivers[-1]["affected_percentage"] = 100.0 * drivers[-1]["affected_points"] / len(values)

    strengths = sorted(
        (row for row in drivers if row["mean_effect"] > EFFECT_THRESHOLD),
        key=lambda row: row["mean_effect"],
        reverse=True,
    )[:4]
    constraints = sorted(
        (row for row in drivers if row["mean_effect"] < -EFFECT_THRESHOLD),
        key=lambda row: row["mean_effect"],
    )[:4]

    pressures = [
        {
            "domain": domain,
            "mean_negative_effect": values["magnitude"] / len(explained),
            "affected_points": len(values["points"]),
            "points_evaluated": len(explained),
            "affected_percentage": 100.0 * len(values["points"]) / len(explained),
            "features": sorted(values["features"]),
        }
        for domain, values in pressure_domains.items()
    ]
    pressures.sort(key=lambda row: row["mean_negative_effect"], reverse=True)

    return {
        "overview": {
            "total_points": total_points,
            "evaluated_points": evaluated_points,
            "unavailable_points": unavailable_points,
            "coverage_percentage": 100.0 * evaluated_points / total_points if total_points else 0.0,
            "mean_percentile": float(np.mean(percentiles)) if scored else None,
            "median_percentile": float(np.median(percentiles)) if scored else None,
            "min_percentile": min(percentiles) if scored else None,
            "max_percentile": max(percentiles) if scored else None,
            "high_suitability_points": high_points,
            "high_suitability_percentage": 100.0 * high_points / evaluated_points if evaluated_points else 0.0,
            "strongest_point": _compact_point(strongest),
            "strongest_sector": max(
                eligible_sectors,
                key=lambda item: (item["mean_percentile"], item["points"]),
            ) if eligible_sectors else None,
        },
        "habitat": {
            "strengths": strengths,
            "constraints": constraints,
            "explanation_points": len(explained),
            "failed_explanations": failed_explanations,
            "status": ("unavailable" if not explained
                       else "partial" if failed_explanations else "available"),
        },
        "pressures": pressures[:4],
        "distribution": distribution,
        "sectors": sectors,
        "survey_priorities": priorities,
        "warnings": warnings,
    }


def analyze_deep_dive(species, latitude, longitude, radius_km, region="MA", *, username=None):
    points = build_grid(latitude, longitude, radius_km)
    region = get_region(region).code
    record = resolve_model(species, region, username=username)
    model, metrics = load_model_and_metadata(record.species, record)
    predictors = metrics.get("predictor_names")
    if not predictors:
        raise RuntimeError("Saved model metadata does not include predictor names.")
    if not hasattr(model, "predict_proba"):
        raise RuntimeError("Saved model does not support suitability scoring.")

    extractor = extract_features
    if region != "MA":
        from wildlocate.core.regional import SCHEMA, extract_regional_features
        if metrics.get("feature_schema") != SCHEMA or metrics.get("region") != region:
            raise ValueError("This model is not compatible with the selected region.")
        extractor = lambda lat, lon: extract_regional_features(lat, lon, region)

    comparison_scores, comparison = load_comparison_scores(
        model, predictors, record.species, record
    )

    for point in points:
        # Points near state boundaries or outside installed raster coverage should
        # not abort an otherwise valid Deep Dive. They are explicitly unscored.
        try:
            extracted = extractor(point["latitude"], point["longitude"])
            frame = build_prediction_frame(extracted, predictors)
            if not np.isfinite(frame.to_numpy(dtype=float)).all():
                raise ValueError("Environmental feature extraction returned missing values")
            score = float(model.predict_proba(frame)[0, 1])
            if not math.isfinite(score):
                raise ValueError("Prediction returned a non-finite suitability score")
        except Exception as exc:
            logger.warning(
                "Deep Dive sample unavailable at %.5f, %.5f: %s",
                point["latitude"], point["longitude"], exc,
            )
            point.update(status="unavailable",
                         reason="Outside model coverage or environmental data unavailable.")
            continue

        percentile = percentile_of_score(score, comparison_scores)
        point.update(
            status="ok", score=score, percentile=percentile,
            category=category_for_percentile(percentile),
        )
        try:
            point["influences"] = habitat_insights(
                model, frame, comparison, comparison_scores, score, include_scenarios=False
            ).get("influences", [])
            point["insight_status"] = "available"
        except Exception:
            logger.exception("Deep Dive explanation unavailable for a scored sample")
            point["influences"] = []
            point["insight_status"] = "unavailable"

    summary = summarize_deep_dive(points, latitude, longitude)

    def evaluate_place(place_latitude, place_longitude):
        frame = build_prediction_frame(extractor(place_latitude, place_longitude), predictors)
        if not np.isfinite(frame.to_numpy(dtype=float)).all():
            raise ValueError("Environmental feature extraction returned missing values")
        score = float(model.predict_proba(frame)[0, 1])
        if not math.isfinite(score):
            raise ValueError("Prediction returned a non-finite suitability score")
        percentile = percentile_of_score(score, comparison_scores)
        influences = habitat_insights(
            model, frame, comparison, comparison_scores, score, include_scenarios=False
        ).get("influences", [])
        return {"status": "ok", "score": score, "percentile": percentile,
                "category": category_for_percentile(percentile),
                "influences": influences, "insight_status": "available"}

    places = analyze_places(points, latitude, longitude, radius_km, region, evaluate_place)

    public_points = []
    for point in points:
        public = {key: point[key] for key in ("latitude", "longitude", "status")}
        if point["status"] == "ok":
            public.update(
                score=point["score"],
                percentile=point["percentile"],
                category=point["category"],
                insight_status=point["insight_status"],
            )
        else:
            public["reason"] = point.get("reason")
        public_points.append(public)

    return {
        "analysis_type": "deep_dive",
        "species": record.species,
        "region": region,
        "latitude": float(latitude),
        "longitude": float(longitude),
        "radius_km": radius_km,
        "grid_spacing_km": radius_km / 5,
        "evidence": model_evidence(metrics, region),
        "model": format_model_name(metrics.get("selected_model", "Unknown Model")),
        "sample_points": len(points),
        "unavailable_points": len(points) - summary["overview"]["evaluated_points"],
        "points": public_points,
        "places": places,
        **summary,
    }
