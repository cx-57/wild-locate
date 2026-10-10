"""Connect actual mapped places to local, species-specific model comparisons."""
import logging
import math
from datetime import datetime, timezone

from pyproj import Geod

logger = logging.getLogger(__name__)
GEOD = Geod(ellps="WGS84")
MAX_AREAS = 6
SEARCH_RADIUS_M = 1000
MAX_CANDIDATES_PER_KIND = 6
MAX_SITES_PER_KIND = 3
MIN_EFFECT = .001  # 0.1 score points; smaller differences round into noise.
METHOD = (
    "Roads, buildings and mapped habitat polygons are located using "
    "OpenStreetMap or available local data. The species model evaluates environmental "
    "conditions near each feature. Each comparison replaces one environmental "
    "variable at a time with its reference value; correlated effects are not added."
)
LIMITATIONS = (
    "Surrounding conditions are measured at approximately 250 m or 1 km scales. "
    "Model associations cannot establish that an individual road or building "
    "caused ecological damage or predict the effect of removing it. Search "
    "coverage is partial; maps and environmental data can be incomplete or dated."
)


def discover_places(*args, **kwargs):
    from wildlocate.core.mapped_places import discover_places as discover
    return discover(*args, **kwargs)


def _finite(value):
    try:
        return not isinstance(value, bool) and math.isfinite(value)
    except (TypeError, ValueError):
        return False


def _located(item):
    return (_finite(item.get("latitude")) and _finite(item.get("longitude"))
            and -90 <= item["latitude"] <= 90 and -180 <= item["longitude"] <= 180)


def _distance(a, b):
    return abs(GEOD.inv(a["longitude"], a["latitude"],
                        b["longitude"], b["latitude"])[2])


def _relevant(kind, name):
    if not isinstance(name, str):
        return False
    if kind == "building":
        return any(term in name for term in ("developed", "impervious"))
    if kind == "road":
        return any(term in name for term in ("road", "developed", "impervious"))
    return any(term in name for term in (
        "slope", "rugged", "elevation", "terrain", "forest", "wetland", "water",
        "grassland", "shrubland", "barren", "cropland",
    ))


def _negative_signals(assessment, kind, *, terrain_only=False):
    signals = []
    rows = assessment.get("influences")
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name = row.get("feature", "")
        if not isinstance(name, str) or not _relevant(kind, name):
            continue
        if terrain_only and not any(term in name for term in ("slope", "rugged", "elevation", "terrain")):
            continue
        if all(_finite(row.get(key)) for key in ("effect", "current", "reference")) and row["effect"] <= -MIN_EFFECT:
            signals.append({
                "feature": name,
                "current": float(row["current"]),
                "reference": float(row["reference"]),
                "effect": float(row["effect"]),
            })
    return sorted(signals, key=lambda row: (row["effect"], row["feature"]))


def select_search_areas(points, latitude, longitude, radius_km):
    center = {"latitude": latitude, "longitude": longitude}
    usable = [p for p in points if p.get("status") == "ok" and _located(p)
              and _distance(p, center) <= radius_km * 1000 + .1]

    def priority(point, kind=None):
        rows = point.get("influences")
        if not isinstance(rows, list):
            rows = []
        effects = [abs(row["effect"]) for row in rows
                   if isinstance(row, dict) and _finite(row.get("effect")) and row["effect"] < 0
                   and any(_relevant(group, row.get("feature", ""))
                           for group in ((kind,) if kind else ("road", "building", "habitat")))]
        return max(effects, default=0)

    # Examine the chosen center first, not just locations selected by the model.
    areas = [{"latitude": float(latitude), "longitude": float(longitude),
              "radius_m": SEARCH_RADIUS_M}]

    def add_first(candidates):
        for p in candidates:
            if all(_distance(p, existing) >= 1500 for existing in areas):
                areas.append({"latitude": float(p["latitude"]), "longitude": float(p["longitude"]),
                              "radius_m": SEARCH_RADIUS_M})
                return True
        return False

    # Strong natural terrain differences must not displace every built-pressure
    # neighborhood. Inspect both kinds before filling any remaining slots.
    queues = {kind: sorted((p for p in usable if priority(p, kind) >= MIN_EFFECT),
                          key=lambda p: (-priority(p, kind), _distance(p, center), p["latitude"], p["longitude"]))
              for kind in ("building", "road", "habitat")}
    for _ in range(2):
        for kind in ("building", "road", "habitat"):
            add_first(queues[kind])
    fallback = sorted(usable, key=lambda p: (-priority(p), _distance(p, center), p["latitude"], p["longitude"]))
    while len(areas) < MAX_AREAS and add_first(fallback):
        pass
    return areas


def _candidates(features, points, center, radius_km):
    valid = [f for f in features if isinstance(f, dict) and _located(f)
             and f.get("kind") in ("road", "building", "habitat")
             and _distance(f, center) <= radius_km * 1000 + .1]
    selected = []
    seen = set()
    nearby_points = [p for p in points if _located(p) and p.get("status") == "ok"]
    for kind in ("road", "building", "habitat"):
        def priority(candidate):
            # Rank mapped features by nearby model evidence, then prefer closer,
            # larger features when the evidence is tied.
            closest = min(nearby_points, key=lambda p: _distance(p, candidate), default=None)
            signals = _negative_signals(closest or {}, kind)
            strength = -signals[0]["effect"] if signals else 0
            distance = _distance(candidate, closest) if closest else 0
            size = candidate.get("area_m2", candidate.get("length_m", 0))
            return (-strength / (1 + distance / 1000), distance,
                    -size if _finite(size) else 0, str(candidate.get("id", "")))

        group = []
        for f in sorted((f for f in valid if f["kind"] == kind), key=priority):
            if not f.get("id") or f["id"] in seen:
                continue
            if all(_distance(f, old) >= 200 for old in group):
                group.append(f)
                seen.add(f["id"])
                if len(group) == MAX_CANDIDATES_PER_KIND:
                    break
        selected.extend(group)
    return selected


def _terrain_candidates(points):
    """Identify real raster-scored coordinates, not invented named map features."""
    candidates = []
    for point in points:
        if (point.get("status") != "ok" or not _located(point)
                or point.get("insight_status") == "unavailable"):
            continue
        rows = point.get("influences")
        if not isinstance(rows, list):
            continue
        terrain = [row for row in rows if isinstance(row, dict)
                   and isinstance(row.get("feature"), str)
                   and any(term in row["feature"]
                           for term in ("slope", "rugged", "elevation", "terrain"))
                   and all(_finite(row.get(key)) for key in ("effect", "current", "reference"))
                   and abs(row["effect"]) >= MIN_EFFECT]
        if terrain:
            strength = max(abs(row["effect"]) for row in terrain)
            candidates.append((strength, point))
    candidates.sort(key=lambda item: (-item[0], item[1]["latitude"], item[1]["longitude"]))
    selected = []
    for _, point in candidates:
        if any(_distance(point, existing["_sample"]) < 500 for existing in selected):
            continue
        selected.append({
            "id": f"terrain:{point['latitude']:.6f}:{point['longitude']:.6f}",
            "kind": "habitat",
            "name": f"Terrain sample at {point['latitude']:.4f}, {point['longitude']:.4f}",
            "latitude": point["latitude"], "longitude": point["longitude"],
            "geometry": {"type": "Point", "coordinates": [point["longitude"], point["latitude"]]},
            "source": "USGS 3DEP terrain model", "source_url": "https://www.usgs.gov/3d-elevation-program",
            "tags": {}, "_sample": point,
        })
        if len(selected) == 2:
            break
    return selected


def _validate_assessment(assessment):
    if (not isinstance(assessment, dict) or assessment.get("insight_status") == "unavailable"
            or assessment.get("status") == "unavailable"):
        raise ValueError("Model explanations are unavailable")
    if not (_finite(assessment.get("score")) and 0 <= assessment["score"] <= 1
            and _finite(assessment.get("percentile")) and 0 <= assessment["percentile"] <= 100):
        raise ValueError("The local habitat score is unavailable")
    if not isinstance(assessment.get("influences"), list):
        raise ValueError("Model explanations are unavailable")
    for row in assessment["influences"]:
        if not isinstance(row, dict) or not isinstance(row.get("feature"), str) or not all(
                _finite(row.get(key)) for key in ("effect", "current", "reference")):
            raise ValueError("A model comparison contains missing values")


def _field_checks(kind, terrain_sample=False):
    if kind == "road":
        return ["Check whether habitat cover connects across this road",
                "Inspect existing culverts, crossings, and fencing gaps",
                "Look for recorded wildlife collisions and measured traffic before choosing an intervention"]
    if kind == "building":
        return ["Verify the building footprint and surrounding paved surfaces",
                "Check drainage outlets and where runoff travels after rain",
                "Look for vegetation that connects nearby habitat around the developed site"]
    if not terrain_sample:
        return ["Verify the mapped habitat type and current ground cover",
                "Check drainage, wetland edges, and gaps in vegetation",
                "Look for habitat continuity beyond the mapped boundary"]
    return ["Verify the slope, ground cover, and wet or dry drainage breaks",
            "Check whether the mapped habitat continues beyond this location",
            "Treat natural terrain as habitat context rather than evidence of damage"]


def _possible_pathways(kind, terrain_sample=False):
    """Questions suggested by feature type, NOT observed or quantified effects."""
    if kind == "road":
        return [
            "Potential movement barrier or wildlife-vehicle collision risk; requires field or traffic evidence",
            "Possible nearby impervious surface and drainage changes; requires site verification",
        ]
    if kind == "building":
        return [
            "Potential habitat-cover replacement around the footprint; confirm current land cover",
            "Possible runoff or lighting effects; not measured by the habitat model",
        ]
    if terrain_sample:
        return [
            "Slopes and landforms can influence drainage, travel routes and microhabitats; "
            "these effects are not directly measured here",
        ]
    return [
        "Potential habitat connectivity or water/vegetation function; verify current conditions",
    ]


def analyze_places(points, latitude, longitude, radius_km, region, evaluate):
    """Score selected real places; preserve the landscape report on mapping failure."""
    center = {"latitude": latitude, "longitude": longitude}
    areas = select_search_areas(points, latitude, longitude, radius_km)
    report = {
        "status": "unavailable", "summary": "Place-level analysis is unavailable without usable habitat samples",
        "method": METHOD, "limitations": LIMITATIONS, "sources": [],
        "retrieved_at": datetime.now(timezone.utc).isoformat(), "searched_areas": areas,
        "mapped_features": 0, "candidate_features": 0, "assessed_features": 0,
        "failed_evaluations": 0, "sites": [], "warnings": [],
    }
    if not areas:
        return report
    try:
        mapping = discover_places(areas, latitude, longitude, radius_km, region)
        if not isinstance(mapping, dict) or not isinstance(mapping.get("features"), list):
            raise ValueError("Mapped feature data is unavailable")
    except Exception:
        logger.warning("Specific-place mapping unavailable", exc_info=True)
        mapping = {"status": "unavailable", "features": [], "sources": [],
                   "warnings": ["Online mapping is unavailable; roads and buildings could not be inspected"]}
    report.update(
        mapping_status=mapping.get("status", "unavailable"),
        sources=mapping.get("sources", []),
        retrieved_at=mapping.get("retrieved_at", report["retrieved_at"]),
        warnings=list(mapping.get("warnings", [])),
    )
    features = mapping.get("features", [])
    report["mapped_features"] = len(features)
    candidates = _candidates(features, points, center, radius_km)
    # Terrain coordinates are sampled from real elevation rasters, not invented
    # buildings or mapped polygons. Keep their origin clearly distinguished.
    terrain_candidates = [item for item in _terrain_candidates(points)
                          if _distance(item, center) <= radius_km * 1000 + .1]
    candidates.extend(terrain_candidates)
    report["candidate_features"] = len(candidates)
    report["terrain_samples"] = len(terrain_candidates)
    if terrain_candidates:
        report["sources"].append({
            "name": "USGS 3DEP elevation-derived terrain samples",
            "url": "https://www.usgs.gov/3d-elevation-program",
            "coverage": "Only selected, successfully scored geographic coordinates",
        })
    # These points are reused for every candidate, so filter them once instead
    # of rescanning the entire Deep Dive grid for each mapped feature.
    nearby_habitat_points = [
        p for p in points
        if p.get("status") == "ok" and _located(p)
        and _finite(p.get("percentile")) and p["percentile"] >= 60
    ]
    sites = []
    for candidate in candidates:
        site = {key: value for key, value in candidate.items() if not key.startswith("_")}
        is_terrain = "_sample" in candidate
        site.update(signals=[], supporting_signals=[], strongest_signal_points=0.0,
                    assessment_status="unavailable", terrain_sample=is_terrain,
                    potential_pathways=_possible_pathways(candidate["kind"], is_terrain),
                    field_checks=_field_checks(candidate["kind"], is_terrain))
        try:
            assessment = candidate["_sample"] if is_terrain else evaluate(candidate["latitude"], candidate["longitude"])
            _validate_assessment(assessment)
        except Exception:
            report["failed_evaluations"] += 1
            logger.warning("Specific-place model assessment unavailable for %s", candidate.get("id"))
            site["assessment_note"] = (
                "This feature is mapped, but environmental data or model explanations "
                "were unavailable here. No ecological effect can be inferred.")
            sites.append(site)
            continue
        report["assessed_features"] += 1
        negative = _negative_signals(assessment, candidate["kind"], terrain_only=is_terrain)
        positive = sorted(
            ({"feature": row["feature"], "effect": float(row["effect"]),
              "current": float(row["current"]), "reference": float(row["reference"])}
             for row in assessment["influences"]
             if _relevant(candidate["kind"], row["feature"]) and row["effect"] >= MIN_EFFECT
             and (not is_terrain or any(term in row["feature"]
                                       for term in ("slope", "rugged", "elevation", "terrain")))),
            key=lambda row: (-row["effect"], row["feature"]))
        site.update(
            assessment_status="available",
            score=float(assessment["score"]), percentile=float(assessment["percentile"]),
            category=assessment.get("category", ""), signals=negative[:3],
            supporting_signals=positive[:3],
            strongest_signal_points=-negative[0]["effect"] * 100 if negative else 0.0,
            assessment_note=(
                "Nearby environmental conditions have model-linked limiting factors."
                if negative else
                "No substantial limiting model comparison was detected at this place. "
                "That does not mean the mapped feature has no environmental effects."
            ))
        nearest = min(
            nearby_habitat_points,
            key=lambda p: _distance(candidate, p),
            default=None,
        )
        if nearest and _distance(candidate, nearest) <= 2000:
            site["nearby_habitat"] = {"latitude": nearest["latitude"], "longitude": nearest["longitude"],
                                      "distance_m": round(_distance(candidate, nearest)),
                                      "percentile": nearest["percentile"]}
        sites.append(site)
    # Prefer places with evidence of limiting conditions, but never hide real
    # features merely because their modeled association is neutral or unknown.
    sites.sort(key=lambda p: (p["assessment_status"] != "available",
                              -p["strongest_signal_points"], _distance(p, center), str(p["id"])))
    counts = {kind: 0 for kind in ("road", "building", "habitat")}
    terrain_count = 0
    mapped_habitat_count = 0
    for site in sites:
        if counts[site["kind"]] >= MAX_SITES_PER_KIND:
            continue
        if site["kind"] == "habitat":
            if site["terrain_sample"]:
                if terrain_count >= 2:
                    continue
                terrain_count += 1
            else:
                if mapped_habitat_count >= 2:
                    continue
                mapped_habitat_count += 1
        site["rank"] = len(report["sites"]) + 1
        report["sites"].append(site)
        counts[site["kind"]] += 1
    if report["failed_evaluations"]:
        report["warnings"].append(f"Model comparisons were unavailable for {report['failed_evaluations']} selected places")
    if report["sites"]:
        report["status"] = "partial" if report["mapping_status"] != "available" or report["failed_evaluations"] else "available"
        report["summary"] = (
            f"{len(report['sites'])} locations or terrain samples are available for "
            f"inspection; {report['assessed_features']} of {report['candidate_features']} "
            "selected candidates had usable local model assessments."
        )
    elif report["mapping_status"] == "unavailable":
        report["summary"] = "Mapping unavailable: mapped roads and buildings could not be retrieved here; this does not mean they are absent."
    else:
        report["status"] = "no_signal"
        report["summary"] = "No supported mapped places were found in the searched neighborhoods; map coverage is incomplete."
    return report
