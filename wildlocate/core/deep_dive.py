"""Deeper habitat analysis for a completed Wild-Locate assessment."""

import math
from collections import defaultdict

import numpy as np

from wildlocate.core.environment import extract_features
from wildlocate.core.predict import (
    build_grid,
    build_prediction_frame,
    category_for_percentile,
    format_model_name,
    habitat_insights,
    load_comparison_scores,
    load_model_and_metadata,
    percentile_of_score,
)
from wildlocate.core.registry import resolve_model
from wildlocate.core.regional import get_region


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
    names = ("North", "Northeast", "East", "Southeast",
             "South", "Southwest", "West", "Northwest")
    return names[int((angle + 22.5) // 45) % 8]


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
    scored = [point for point in points if point.get("status") == "ok"]
    if not scored:
        return {
            "overview": {
                "evaluated_points": 0,
                "mean_percentile": None,
                "high_suitability_points": 0,
                "strongest_point": None,
                "strongest_sector": None,
            },
            "habitat": {"strengths": [], "constraints": []},
            "pressures": [],
        }

    percentiles = [point["percentile"] for point in scored]
    strongest = max(scored, key=lambda point: (point["percentile"], point["score"]))

    sector_values = defaultdict(list)
    for point in scored:
        sector_values[_sector(
            point["latitude"], point["longitude"], center_latitude, center_longitude
        )].append(point["percentile"])
    sectors = [
        {"name": name, "mean_percentile": float(np.mean(values)), "points": len(values)}
        for name, values in sector_values.items()
        if name != "Center" and len(values) >= 3
    ]

    effects = defaultdict(list)
    pressure_domains = defaultdict(lambda: {"magnitude": 0.0, "points": set(), "features": set()})
    for point_index, point in enumerate(scored):
        for influence in point.get("influences", []):
            name = influence["feature"]
            effect = float(influence["effect"])
            if not math.isfinite(effect):
                continue
            effects[name].append(effect)
            if effect < 0:
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
                value > 0 if mean_effect > 0 else value < 0
                for value in values
            ),
            "points_evaluated": len(values),
        })

    strengths = sorted(
        (row for row in drivers if row["mean_effect"] > 1e-8),
        key=lambda row: row["mean_effect"],
        reverse=True,
    )[:4]
    constraints = sorted(
        (row for row in drivers if row["mean_effect"] < -1e-8),
        key=lambda row: row["mean_effect"],
    )[:4]

    pressures = [
        {
            "domain": domain,
            "mean_negative_effect": values["magnitude"] / len(scored),
            "affected_points": len(values["points"]),
            "affected_percentage": 100.0 * len(values["points"]) / len(scored),
            "features": sorted(values["features"]),
        }
        for domain, values in pressure_domains.items()
    ]
    pressures.sort(key=lambda row: row["mean_negative_effect"], reverse=True)

    return {
        "overview": {
            "evaluated_points": len(scored),
            "mean_percentile": float(np.mean(percentiles)),
            "high_suitability_points": sum(value >= 60 for value in percentiles),
            "strongest_point": _compact_point(strongest),
            "strongest_sector": max(
                sectors,
                key=lambda item: (item["mean_percentile"], item["points"]),
            ) if sectors else None,
        },
        "habitat": {
            "strengths": strengths,
            "constraints": constraints,
        },
        "pressures": pressures[:4],
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
        try:
            extracted = extractor(point["latitude"], point["longitude"])
            frame = build_prediction_frame(extracted, predictors)
            if not np.isfinite(frame.to_numpy(dtype=float)).all():
                raise ValueError("Environmental feature extraction returned missing values")
        except ValueError as exc:
            if not str(exc).startswith((
                "Requested coordinate cannot be evaluated",
                "Environmental feature extraction returned missing values",
            )):
                raise
            point.update(
                status="unavailable",
                reason="Outside model coverage or incomplete environmental data.",
            )
            continue

        score = float(model.predict_proba(frame)[0, 1])
        if not math.isfinite(score):
            raise RuntimeError("Prediction returned a non-finite suitability score.")

        percentile = percentile_of_score(score, comparison_scores)
        point.update(
            status="ok",
            score=score,
            percentile=percentile,
            category=category_for_percentile(percentile),
        )
        try:
            point["influences"] = habitat_insights(
                model, frame, comparison, comparison_scores, score
            ).get("influences", [])
        except Exception:
            point["influences"] = []

    summary = summarize_deep_dive(points, latitude, longitude)

    public_points = []
    for point in points:
        public = {key: point[key] for key in ("latitude", "longitude", "status")}
        if point["status"] == "ok":
            public.update(
                score=point["score"],
                percentile=point["percentile"],
                category=point["category"],
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
        "model": format_model_name(metrics.get("selected_model", "Unknown Model")),
        "sample_points": len(points),
        "unavailable_points": len(points) - summary["overview"]["evaluated_points"],
        "points": public_points,
        **summary,
    }
