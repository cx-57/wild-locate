"""Second-stage conservation research analysis for a completed habitat assessment.

A Deep Dive deliberately does more work than the ordinary assessment. It samples
the selected landscape, interprets the fitted species model at each usable point,
compares strong and weak habitat, and summarizes modeled pressure signals and
counterfactual scenarios. It does not claim causality or management priority.
"""

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
    return "Other environmental factors"


def _sector(latitude, longitude, center_latitude, center_longitude):
    """Return an eight-way compass sector around the selected center."""
    north = math.radians(latitude - center_latitude) * 6371.0
    east = (
        math.radians(longitude - center_longitude)
        * 6371.0
        * math.cos(math.radians(center_latitude))
    )
    distance = math.hypot(north, east)
    if distance < 0.05:
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


def summarize_deep_dive(points, predictors, center_latitude, center_longitude):
    """Summarize already-scored Deep Dive sample points.

    Feature effects come from the existing Wild-Locate interpretation method:
    replace one feature with its comparison median and recompute the fitted
    model. Positive/negative values therefore describe model sensitivity around
    the sampled conditions, not ecological causation.
    """
    scored = [point for point in points if point.get("status") == "ok"]
    if not scored:
        return {
            "overview": {
                "evaluated_points": 0,
                "mean_percentile": None,
                "median_percentile": None,
                "high_suitability_points": 0,
                "very_high_suitability_points": 0,
                "strongest_point": None,
                "weakest_point": None,
                "strongest_sector": None,
            },
            "habitat": {"strengths": [], "constraints": [], "contrasts": []},
            "pressures": [],
            "scenarios": [],
        }

    percentiles = [point["percentile"] for point in scored]
    strongest = max(scored, key=lambda point: (point["percentile"], point["score"]))
    weakest = min(scored, key=lambda point: (point["percentile"], point["score"]))

    sector_values = defaultdict(list)
    for point in scored:
        sector_values[_sector(
            point["latitude"], point["longitude"], center_latitude, center_longitude
        )].append(point["percentile"])
    sector_rows = [
        {
            "name": name,
            "mean_percentile": float(np.mean(values)),
            "points": len(values),
        }
        for name, values in sector_values.items()
        if name != "Center" and len(values) >= 3
    ]
    strongest_sector = (
        max(sector_rows, key=lambda item: (item["mean_percentile"], item["points"]))
        if sector_rows else None
    )

    feature_effects = defaultdict(list)
    feature_values = defaultdict(list)
    negative_domains = defaultdict(lambda: {"magnitude": 0.0, "points": set(), "features": set()})
    for point_index, point in enumerate(scored):
        for name, value in (point.get("features") or {}).items():
            if isinstance(value, (int, float)) and math.isfinite(value):
                feature_values[name].append(float(value))
        for influence in point.get("influences", []):
            name = influence["feature"]
            effect = float(influence["effect"])
            if not math.isfinite(effect):
                continue
            feature_effects[name].append(effect)
            if effect < 0:
                domain = _feature_domain(name)
                negative_domains[domain]["magnitude"] += -effect
                negative_domains[domain]["points"].add(point_index)
                negative_domains[domain]["features"].add(name)

    driver_rows = []
    for name, effects in feature_effects.items():
        if not effects:
            continue
        mean_effect = float(np.mean(effects))
        row = {
            "feature": name,
            "mean_effect": mean_effect,
            "affected_points": sum(
                (effect > 0 if mean_effect > 0 else effect < 0)
                for effect in effects
            ),
            "points_evaluated": len(effects),
            "median_value": (
                float(np.median(feature_values[name]))
                if feature_values.get(name) else None
            ),
        }
        driver_rows.append(row)

    strengths = sorted(
        (row for row in driver_rows if row["mean_effect"] > 1e-8),
        key=lambda row: row["mean_effect"],
        reverse=True,
    )[:5]
    constraints = sorted(
        (row for row in driver_rows if row["mean_effect"] < -1e-8),
        key=lambda row: row["mean_effect"],
    )[:5]

    pressures = []
    total = len(scored)
    for domain, values in negative_domains.items():
        pressures.append({
            "domain": domain,
            "mean_negative_effect": values["magnitude"] / total,
            "affected_points": len(values["points"]),
            "affected_percentage": 100.0 * len(values["points"]) / total,
            "features": sorted(values["features"]),
        })
    pressures.sort(key=lambda row: row["mean_negative_effect"], reverse=True)
    pressures = pressures[:5]

    # Compare the highest- and lowest-suitability fifths. Standardizing the
    # median difference makes differently scaled predictors comparable.
    ordered = sorted(scored, key=lambda point: (point["percentile"], point["score"]))
    group_size = max(3, math.ceil(len(ordered) * 0.2))
    low_group, high_group = ordered[:group_size], ordered[-group_size:]
    contrasts = []
    for name in predictors:
        all_values = [
            float(point["features"][name]) for point in scored
            if name in point.get("features", {})
            and math.isfinite(float(point["features"][name]))
        ]
        high_values = [
            float(point["features"][name]) for point in high_group
            if name in point.get("features", {})
            and math.isfinite(float(point["features"][name]))
        ]
        low_values = [
            float(point["features"][name]) for point in low_group
            if name in point.get("features", {})
            and math.isfinite(float(point["features"][name]))
        ]
        if not all_values or not high_values or not low_values:
            continue
        spread = float(np.std(all_values))
        if spread <= 1e-12:
            continue
        high_median = float(np.median(high_values))
        low_median = float(np.median(low_values))
        contrasts.append({
            "feature": name,
            "high_habitat_median": high_median,
            "low_habitat_median": low_median,
            "difference": high_median - low_median,
            "standardized_difference": (high_median - low_median) / spread,
        })
    contrasts.sort(key=lambda row: abs(row["standardized_difference"]), reverse=True)
    contrasts = contrasts[:6]

    scenarios = []
    for point in scored:
        for scenario in point.get("scenarios", []):
            projected = int(scenario["percentile"])
            delta = projected - point["percentile"]
            if delta <= 0:
                continue
            scenarios.append({
                "latitude": point["latitude"],
                "longitude": point["longitude"],
                "current_percentile": point["percentile"],
                "projected_percentile": projected,
                "percentile_delta": delta,
                "description": scenario["title"],
                "score_delta": float(scenario["delta"]),
                "changes": scenario.get("changes", []),
            })
    scenarios.sort(
        key=lambda row: (row["percentile_delta"], row["score_delta"]),
        reverse=True,
    )

    return {
        "overview": {
            "evaluated_points": len(scored),
            "mean_percentile": float(np.mean(percentiles)),
            "median_percentile": float(np.median(percentiles)),
            "high_suitability_points": sum(value >= 60 for value in percentiles),
            "very_high_suitability_points": sum(value >= 80 for value in percentiles),
            "strongest_point": _compact_point(strongest),
            "weakest_point": _compact_point(weakest),
            "strongest_sector": strongest_sector,
        },
        "habitat": {
            "strengths": strengths,
            "constraints": constraints,
            "contrasts": contrasts,
        },
        "pressures": pressures,
        "scenarios": scenarios[:5],
    }


def analyze_deep_dive(species, latitude, longitude, radius_km, region="MA", *, username=None):
    """Run the heavier second-stage landscape analysis."""
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
            features={name: float(frame.iloc[0][name]) for name in predictors},
        )
        try:
            insights = habitat_insights(
                model, frame, comparison, comparison_scores, score
            )
            point["influences"] = insights.get("influences", [])
            point["scenarios"] = insights.get("scenarios", [])
        except Exception:
            # A failed explanation should not erase an otherwise valid model
            # prediction from the landscape summary.
            point["influences"] = []
            point["scenarios"] = []
            point["insights_error"] = True

    summary = summarize_deep_dive(
        points, predictors, latitude, longitude
    )
    public_points = []
    for point in points:
        public = {
            key: point[key] for key in ("latitude", "longitude", "status")
        }
        if point["status"] == "ok":
            public.update(
                score=point["score"],
                percentile=point["percentile"],
                category=point["category"],
                top_influences=point.get("influences", [])[:3],
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
        "model": format_model_name(metrics.get("selected_model", "Unknown Model")),
        "training_observations": int(metrics.get("presence_count", 0)),
        "sample_points": len(points),
        "unavailable_points": len(points) - summary["overview"]["evaluated_points"],
        "points": public_points,
        **summary,
        "data_scope": {
            "predictors": list(predictors),
            "connected": [
                "species occurrence data",
                "land cover",
                "impervious surface",
                "terrain/elevation",
                "water context",
                "road context",
            ],
            "not_connected_yet": [
                "protected-area boundaries",
                "historical land-cover change",
            ],
        },
        "limitations": [
            "Deep Dive samples 81 locations; it is not continuous habitat coverage.",
            "Feature effects are model interpretations relative to comparison medians, not causal ecological effects.",
            "Pressure signals summarize negative model effects and should not be treated as confirmed threats without ecological validation.",
            "Counterfactual scenarios are model experiments, not management recommendations.",
            "Protected-area status and historical land-cover change are not included in this version.",
        ],
    }


def answer_deep_dive_question(report, question):
    """Deterministically explain a completed report without inventing new evidence."""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("Enter a question about this Deep Dive.")
    question = question.strip()
    if len(question) > 300:
        raise ValueError("Keep the question under 300 characters.")

    lowered = question.casefold()
    overview = report.get("overview", {})
    habitat = report.get("habitat", {})
    pressures = report.get("pressures", [])
    scenarios = report.get("scenarios", [])

    if any(word in lowered for word in ("threat", "pressure", "risk", "weak")):
        if not pressures:
            return "No consistent modeled pressure signal was found across the evaluated samples."
        top = pressures[0]
        return (
            f"The strongest modeled pressure signal is {top['domain'].lower()}. "
            f"It appears as a negative model effect at {top['affected_points']} of "
            f"{overview.get('evaluated_points', 0)} evaluated locations. This is a "
            "model-based signal, not confirmation of an ecological threat."
        )

    if any(word in lowered for word in ("strength", "good", "help", "why high")):
        strengths = habitat.get("strengths", [])
        if not strengths:
            return "The model did not show a consistent positive regional driver relative to its comparison medians."
        names = ", ".join(row["feature"].replace("_", " ") for row in strengths[:3])
        return (
            f"The most consistent positive model drivers in this Deep Dive are {names}. "
            "They describe conditions that raise the fitted model score relative to replacing those features with comparison medians."
        )

    if any(word in lowered for word in ("restore", "scenario", "improve", "intervention")):
        if not scenarios:
            return "None of the tested forest or impervious-surface scenarios produced a positive percentile change at the sampled locations."
        top = scenarios[0]
        return (
            f"The largest tested model response is near "
            f"{abs(top['latitude']):.4f}° {'N' if top['latitude'] >= 0 else 'S'}, "
            f"{abs(top['longitude']):.4f}° {'E' if top['longitude'] >= 0 else 'W'}: "
            f"{top['current_percentile']}th to {top['projected_percentile']}th percentile "
            f"under the scenario '{top['description']}'. This is a counterfactual model experiment, not a recommended intervention."
        )

    if any(word in lowered for word in ("where", "strongest", "best area", "best habitat")):
        point = overview.get("strongest_point")
        sector = overview.get("strongest_sector")
        if not point:
            return "No sampled location had enough environmental data to identify the strongest modeled habitat."
        sector_text = (
            f" The strongest broad sector was {sector['name'].lower()} "
            f"with a mean percentile of {sector['mean_percentile']:.0f}."
            if sector else ""
        )
        return (
            f"The strongest sampled location is at {point['percentile']}th percentile "
            f"near {abs(point['latitude']):.4f}° {'N' if point['latitude'] >= 0 else 'S'}, "
            f"{abs(point['longitude']):.4f}° {'E' if point['longitude'] >= 0 else 'W'}."
            + sector_text
        )

    mean = overview.get("mean_percentile")
    evaluated = overview.get("evaluated_points", 0)
    if mean is None:
        return "This Deep Dive did not have enough environmental coverage to summarize the selected area."
    return (
        f"Wild-Locate evaluated {evaluated} sampled locations with a mean relative "
        f"habitat percentile of {mean:.0f}. Ask about habitat strengths, modeled "
        "pressures, the strongest area, or restoration scenarios for a more specific explanation."
    )
