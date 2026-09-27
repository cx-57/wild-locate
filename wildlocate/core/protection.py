"""Protected-area context for Conservation Deep Dive.

Queries the public USGS PAD-US 4.1 web service at sampled locations. The result
is intentionally sample-based: it does not estimate protected land area and a
point outside PAD-US is not automatically "unprotected".
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


PADUS_QUERY_URL = (
    "https://edits.nationalmap.gov/arcgis/rest/services/"
    "PAD-US/PAD_US_4_1/MapServer/0/query"
)
PADUS_SOURCE = "USGS Protected Areas Database of the United States (PAD-US) 4.1"

_OUT_FIELDS = ",".join(
    (
        "Unit_Nm",
        "Loc_Nm",
        "GAP_Sts",
        "Mang_Name",
        "Loc_Mang",
        "Des_Tp",
        "Loc_Ds",
        "Pub_Access",
        "Category",
    )
)


def _clean(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _query_point(point, timeout=4):
    response = requests.get(
        PADUS_QUERY_URL,
        params={
            "f": "json",
            "where": "1=1",
            "geometry": f"{point['longitude']},{point['latitude']}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": _OUT_FIELDS,
            "returnGeometry": "false",
        },
        timeout=timeout,
        headers={"User-Agent": "Wild-Locate conservation research"},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("error"):
        raise ValueError("PAD-US returned an invalid response.")

    records = []
    for feature in payload.get("features", []):
        attributes = feature.get("attributes") or {}
        gap_status = _clean(attributes.get("GAP_Sts"))
        records.append(
            {
                "name": _clean(attributes.get("Unit_Nm"))
                or _clean(attributes.get("Loc_Nm")),
                "manager": _clean(attributes.get("Loc_Mang"))
                or _clean(attributes.get("Mang_Name")),
                "designation": _clean(attributes.get("Loc_Ds"))
                or _clean(attributes.get("Des_Tp")),
                "category": _clean(attributes.get("Category")),
                "gap_status": gap_status,
                "public_access": _clean(attributes.get("Pub_Access")),
            }
        )
    return records


def _biodiversity_managed(records):
    """GAP Status 1 or 2 indicates management intent focused on biodiversity."""
    for record in records:
        value = record.get("gap_status")
        if value and value.lstrip().startswith(("1", "2")):
            return True
    return False


def analyze_protection_context(points, *, max_workers=16):
    """Query PAD-US for high/very-high sampled habitat locations only.

    Returns a partial result when individual network calls fail. This keeps an
    external web service from invalidating the species-model Deep Dive.
    """
    targets = [
        (index, point)
        for index, point in enumerate(points)
        if point.get("status") == "ok" and point.get("percentile", -1) >= 60
    ]
    if not targets:
        return {
            "status": "available",
            "source": PADUS_SOURCE,
            "source_service": PADUS_QUERY_URL.rsplit("/query", 1)[0],
            "high_suitability_samples": 0,
            "checked_samples": 0,
            "failed_queries": 0,
            "intersecting_padus": 0,
            "biodiversity_managed": 0,
            "not_intersecting_padus": 0,
            "samples": [],
        }

    results = {}
    failures = set()
    workers = max(1, min(max_workers, len(targets)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_query_point, point): (index, point)
            for index, point in targets
        }
        for future in as_completed(futures):
            index, point = futures[future]
            try:
                records = future.result()
            except (requests.RequestException, ValueError, TypeError):
                failures.add(index)
                continue
            results[index] = {
                "point_index": index,
                "latitude": point["latitude"],
                "longitude": point["longitude"],
                "percentile": point["percentile"],
                "category": point["category"],
                "within_padus": bool(records),
                "biodiversity_managed": _biodiversity_managed(records),
                "areas": records[:4],
            }

    ordered = [results[index] for index, _point in targets if index in results]
    if not ordered:
        return {
            "status": "unavailable",
            "source": PADUS_SOURCE,
            "source_service": PADUS_QUERY_URL.rsplit("/query", 1)[0],
            "high_suitability_samples": len(targets),
            "checked_samples": 0,
            "failed_queries": len(failures),
            "intersecting_padus": 0,
            "biodiversity_managed": 0,
            "not_intersecting_padus": 0,
            "samples": [],
            "message": "PAD-US protection context could not be retrieved.",
        }

    intersecting = sum(sample["within_padus"] for sample in ordered)
    biodiversity = sum(sample["biodiversity_managed"] for sample in ordered)
    return {
        "status": "partial" if failures else "available",
        "source": PADUS_SOURCE,
        "source_service": PADUS_QUERY_URL.rsplit("/query", 1)[0],
        "high_suitability_samples": len(targets),
        "checked_samples": len(ordered),
        "failed_queries": len(failures),
        "intersecting_padus": intersecting,
        "biodiversity_managed": biodiversity,
        "not_intersecting_padus": len(ordered) - intersecting,
        "samples": ordered,
    }
