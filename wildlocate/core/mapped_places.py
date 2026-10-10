"""Bounded retrieval of real mapped roads, footprints and habitat polygons.

Mapping describes features recorded by the source, never an inventory of every
structure or an estimate of ecological damage. Geometry and reported sizes are
restricted to the inspected neighborhoods inside the selected study circle.
"""
import hashlib
import json
import math
import os
import queue
import tempfile
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

import requests
from pyproj import Transformer
from shapely.errors import GEOSException
from shapely.geometry import LineString, MultiLineString, Point, Polygon, mapping
from shapely.ops import polygonize_full, transform, unary_union

from wildlocate.core import environment


OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OSM_URL = "https://www.openstreetmap.org"
MASSDOT_URL = "https://www.mass.gov/info-details/massgis-data-massachusetts-department-of-transportation-massdot-roads"
MAX_RESPONSE_BYTES = 6 * 1024 * 1024
MAX_CACHE_BYTES = 3 * 1024 * 1024
MAX_ELEMENTS = 6000
MAX_FEATURES = 500
CACHE_TTL_SECONDS = 24 * 60 * 60
REQUEST_DEADLINE_SECONDS = 30
ROAD_TYPES = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "residential",
    "unclassified", "service", "living_street", "track", "motorway_link",
    "trunk_link", "primary_link", "secondary_link", "tertiary_link",
}
TAG_KEYS = {
    "name", "ref", "highway", "building", "natural", "landuse", "water",
    "wetland", "access", "surface", "bridge", "tunnel", "leaf_type", "type",
    "addr:street", "operator",
}
COVERAGE_WARNING = (
    "Map coverage is partial: only the listed search neighborhoods were inspected, "
    "and OpenStreetMap may omit buildings, roads or habitat features"
)


class _MappingUnavailable(ValueError):
    pass


def _timestamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value):
    if isinstance(value, bool):
        raise ValueError("Boolean is not a coordinate")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Coordinate is not finite")
    return number


def _coordinate(latitude, longitude):
    latitude, longitude = _number(latitude), _number(longitude)
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("Coordinate is outside geographic bounds")
    return latitude, longitude


def _search_geometry(areas, latitude, longitude, radius_km):
    latitude, longitude = _coordinate(latitude, longitude)
    radius_km = _number(radius_km)
    if not 0 < radius_km <= 100:
        raise ValueError("Study radius is outside supported bounds")
    local_crs = f"+proj=aeqd +lat_0={latitude} +lon_0={longitude} +datum=WGS84 +units=m"
    forward = Transformer.from_crs("EPSG:4326", local_crs, always_xy=True).transform
    reverse = Transformer.from_crs(local_crs, "EPSG:4326", always_xy=True).transform
    study = Point(0, 0).buffer(radius_km * 1000, quad_segs=64)
    normalized, circles = [], []
    if not isinstance(areas, (list, tuple)):
        raise ValueError("Search neighborhoods must be a list")
    for area in areas[:100]:
        if len(normalized) == 6:
            break
        try:
            if not isinstance(area, dict):
                continue
            lat, lon = _coordinate(area["latitude"], area["longitude"])
            radius = min(1000.0, _number(area.get("radius_m", 1000)))
            if radius <= 0:
                continue
            item = {"latitude": lat, "longitude": lon, "radius_m": radius}
            if item in normalized:
                continue
            x, y = forward(lon, lat)
            if not math.isfinite(x) or not math.isfinite(y):
                continue
            circle = Point(x, y).buffer(radius, quad_segs=32).intersection(study)
            if circle.is_empty or circle.area <= 0:
                continue
            normalized.append(item)
            circles.append(circle)
        except (KeyError, TypeError, ValueError):
            continue
    if not normalized:
        raise ValueError("No usable search neighborhoods")
    return normalized, local_crs, forward, reverse, unary_union(circles)


def _query(areas):
    roads = "|".join(sorted(ROAD_TYPES))
    clauses = []
    for area in areas:
        around = f'(around:{area["radius_m"]:.1f},{area["latitude"]:.7f},{area["longitude"]:.7f})'
        clauses.extend([
            f'way["highway"~"^({roads})$"]{around};',
            f'way["building"]["building"!="no"]{around};',
            f'relation["type"="multipolygon"]["building"]["building"!="no"]{around};',
            f'way["natural"~"^(wood|wetland|water|bare_rock)$"]{around};',
            f'relation["type"="multipolygon"]["natural"~"^(wood|wetland|water|bare_rock)$"]{around};',
            f'way["landuse"="forest"]{around};',
            f'relation["type"="multipolygon"]["landuse"="forest"]{around};',
        ])
    return "[out:json][timeout:25][maxsize:67108864];(" + "".join(clauses) + ");out body geom;"


def _fetch_stream(areas, deadline, aborted):
    with requests.post(
        OVERPASS_URL, data={"data": _query(areas)}, timeout=(5, 25), stream=True,
        allow_redirects=False, headers={"User-Agent": "Wild-Locate/0.2 mapped-place analysis", "Accept": "application/json"},
    ) as response:
        response.raise_for_status()
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_RESPONSE_BYTES:
                    raise _MappingUnavailable("Mapping response exceeds the size limit")
            except (TypeError, ValueError) as exc:
                raise _MappingUnavailable("Invalid mapping response size") from exc
        chunks, size = [], 0
        for chunk in response.iter_content(64 * 1024):
            if aborted.is_set() or time.monotonic() > deadline:
                raise _MappingUnavailable("Mapping request exceeded its time limit")
            if not chunk:
                continue
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise _MappingUnavailable("Mapping response exceeds the size limit")
            chunks.append(chunk)
        try:
            return json.loads(b"".join(chunks))
        except (UnicodeDecodeError, ValueError) as exc:
            raise _MappingUnavailable("Mapping service returned invalid data") from exc


def _fetch(areas):
    """Bound the caller's wait even if a slow server never completes a chunk.

    Requests' read timeout measures socket inactivity, not total request time.
    A daemon worker therefore enforces the overall wait while an abort guard
    closes its bounded response when the current socket read next returns.
    """
    deadline = time.monotonic() + REQUEST_DEADLINE_SECONDS
    aborted = threading.Event()
    outcome = queue.Queue(maxsize=1)

    def run():
        try:
            outcome.put((True, _fetch_stream(areas, deadline, aborted)))
        except Exception as exc:
            outcome.put((False, exc))

    threading.Thread(target=run, name="wildlocate-mapped-places", daemon=True).start()
    try:
        succeeded, value = outcome.get(timeout=REQUEST_DEADLINE_SECONDS)
    except queue.Empty as exc:
        aborted.set()
        raise _MappingUnavailable("Mapping request exceeded its time limit") from exc
    if not succeeded:
        raise value
    return value


def _validate_payload(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise _MappingUnavailable("Mapping service returned an invalid feature list")
    if payload.get("remark"):
        raise _MappingUnavailable("Mapping service returned an incomplete response")
    elements = payload["elements"]
    if len(elements) > MAX_ELEMENTS:
        raise _MappingUnavailable("Mapping response exceeds the feature limit")
    if any(not isinstance(element, dict) for element in elements):
        raise _MappingUnavailable("Mapping response contains malformed elements")
    return elements


def _cache_path():
    # One bounded cache file, so repeated queries cannot fill a small disk.
    return environment.get_user_data_dir() / "cache" / "mapped-places.json"


def _cache_key(areas, latitude, longitude, radius_km):
    raw = json.dumps([areas, latitude, longitude, radius_km], sort_keys=True, allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _read_cache(key, now):
    try:
        path = _cache_path()
        if path.stat().st_size > MAX_CACHE_BYTES:
            return None
        cached = json.loads(path.read_bytes())
        epoch = _number(cached["epoch"])
        if cached.get("key") != key or not 0 <= now - epoch < CACHE_TTL_SECONDS:
            return None
        _validate_payload(cached["payload"])
        return cached["payload"], epoch
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _write_cache(key, payload, epoch):
    raw = json.dumps({"key": key, "epoch": epoch, "payload": payload}, allow_nan=False, separators=(",", ":")).encode()
    if len(raw) > MAX_CACHE_BYTES:
        return
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix="mapped-places-", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            stream.write(raw)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _coordinates(raw):
    if not isinstance(raw, list) or not 2 <= len(raw) <= 20000:
        raise ValueError("Geometry has an invalid vertex count")
    coordinates = []
    for point in raw:
        lat, lon = _coordinate(point["lat"], point["lon"])
        coordinates.append((lon, lat))
    return coordinates


def _kind(tags):
    if tags.get("highway") in ROAD_TYPES:
        return "road"
    if tags.get("building") and tags["building"] != "no":
        return "building"
    if tags.get("natural") in {"wood", "wetland", "water", "bare_rock"} or tags.get("landuse") == "forest":
        return "habitat"
    return None


def _semantic_class(kind, tags):
    if kind == "habitat":
        natural = tags.get("natural")
        label = "woodland" if natural == "wood" or (natural is None and tags.get("landuse") == "forest") else natural
        return kind, label
    return kind, tags.get("highway" if kind == "road" else "building")


def _rings(lines):
    polygons, cuts, dangles, invalid = polygonize_full(MultiLineString(lines))
    if polygons.is_empty or not cuts.is_empty or not dangles.is_empty or not invalid.is_empty:
        raise ValueError("Multipolygon rings are incomplete")
    return [polygon for polygon in polygons.geoms]


def _element_geometry(element, kind):
    if element["type"] == "way":
        coordinates = _coordinates(element.get("geometry"))
        if kind == "road":
            return LineString(coordinates), []
        if len(coordinates) < 4 or coordinates[0] != coordinates[-1]:
            raise ValueError("Footprint does not have a closed boundary")
        return Polygon(coordinates), []
    if element["type"] != "relation" or kind == "road":
        return None, []
    members = element.get("members")
    if not isinstance(members, list) or not 0 < len(members) <= MAX_ELEMENTS:
        raise ValueError("Multipolygon has invalid members")
    outer, inner, member_ids = [], [], []
    for member in members:
        if not isinstance(member, dict):
            raise ValueError("Multipolygon member is invalid")
        role = member.get("role", "")
        if member.get("type") != "way" or role not in {"", "outer", "inner"}:
            continue
        coordinates = _coordinates(member.get("geometry"))
        (inner if role == "inner" else outer).append(coordinates)
        member_ids.append(member.get("ref"))
    # Polygonizing nested outer rings gives the larger polygon a hole for the
    # island. Use the exterior rings and assign each inner to its smallest
    # enclosing shell, then union the separately retained island polygons.
    shells = [Polygon(shell.exterior.coords) for shell in _rings(outer)]
    holes = _rings(inner) if inner else []
    assignments = defaultdict(list)
    for hole in holes:
        containing = [index for index, shell in enumerate(shells) if shell.covers(hole)]
        if not containing:
            raise ValueError("Multipolygon has an unmatched inner ring")
        index = min(containing, key=lambda index: shells[index].area)
        assignments[index].append(hole.exterior.coords)
    polygons = [Polygon(shell.exterior.coords, assignments[index]) for index, shell in enumerate(shells)]
    return unary_union(polygons), member_ids


def _finite_geometry(geojson):
    def finite(coordinates):
        if not isinstance(coordinates, (list, tuple)):
            return False
        if coordinates and isinstance(coordinates[0], (int, float)):
            return len(coordinates) == 2 and all(math.isfinite(value) for value in coordinates)
        return bool(coordinates) and all(finite(value) for value in coordinates)
    return finite(geojson.get("coordinates"))


def _clipped_geometry(geometry, kind, forward, reverse, inspected):
    if geometry is None or geometry.is_empty or not geometry.is_valid:
        raise ValueError("Mapped geometry is invalid")
    projected = transform(forward, geometry)
    if not projected.is_valid or projected.is_empty:
        raise ValueError("Projected geometry is invalid")
    clipped = projected.intersection(inspected)
    if clipped.is_empty:
        return None
    allowed = (
        {"LineString", "MultiLineString"}
        if kind == "road"
        else {"Polygon", "MultiPolygon"}
    )
    if clipped.geom_type == "GeometryCollection":
        parts = [part for part in clipped.geoms if part.geom_type in allowed and not part.is_empty]
        if not parts:
            return None
        clipped = unary_union(parts)
    if clipped.geom_type not in allowed or not clipped.is_valid:
        return None
    measure = clipped.length if kind == "road" else clipped.area
    if not math.isfinite(measure) or measure <= 0:
        return None
    point = clipped.representative_point()
    lon, lat = reverse(point.x, point.y)
    lat, lon = _coordinate(lat, lon)
    geographic = mapping(transform(reverse, clipped))
    if not _finite_geometry(geographic):
        raise ValueError("Clipped geographic geometry is not finite")
    return {"latitude": lat, "longitude": lon, "geometry": geographic,
            "length_m" if kind == "road" else "area_m2": round(measure, 2),
            "size_scope": "within_inspected_area"}


def _feature_name(kind, tags):
    if tags.get("name"):
        return tags["name"]
    if kind == "road":
        return tags.get("ref") or f'Unnamed {tags.get("highway", "mapped").replace("_", " ")} road'
    if kind == "building":
        label = tags.get("building", "yes")
        return "Mapped building" if label == "yes" else f'Mapped {label.replace("_", " ")}'
    return {"wood": "Woodland", "wetland": "Wetland", "water": "Water body", "bare_rock": "Rocky terrain"}.get(tags.get("natural"), "Forest")


def _parse_features(elements, areas, forward, reverse, inspected):
    features, seen, represented_ways = [], set(), set()
    skipped = 0
    # Relations first, so standalone ways that form their rings are not counted twice.
    ordered = sorted(enumerate(elements), key=lambda pair: pair[1].get("type") != "relation")
    for original_index, element in ordered:
        kind = None
        try:
            element_type = element.get("type")
            if element_type not in {"way", "relation"}:
                continue
            identifier = element.get("id")
            if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0:
                raise ValueError("Invalid map identifier")
            feature_id = f"osm:{element_type}:{identifier}"
            if feature_id in seen:
                continue
            seen.add(feature_id)
            raw_tags = element.get("tags", {})
            if not isinstance(raw_tags, dict):
                raise ValueError("Invalid map tags")
            tags = {
                tag_key: value[:240]
                for tag_key, value in raw_tags.items()
                if tag_key in TAG_KEYS and isinstance(value, str)
            }
            kind = _kind(tags)
            if kind is None:
                continue
            semantic_class = _semantic_class(kind, tags)
            if element_type == "way" and (identifier, semantic_class) in represented_ways:
                continue
            if element_type == "relation" and tags.get("type") != "multipolygon":
                continue
            geometry, member_ids = _element_geometry(element, kind)
            details = _clipped_geometry(geometry, kind, forward, reverse, inspected)
            if details is None:
                continue
            represented_ways.update((member_id, semantic_class) for member_id in member_ids)
            features.append((original_index, {"id": feature_id, "kind": kind, "name": _feature_name(kind, tags),
                "source": "OpenStreetMap", "source_url": f"{OSM_URL}/{element_type}/{identifier}",
                "tags": tags, **details}))
        except (ValueError, TypeError, KeyError, OverflowError, GEOSException):
            skipped += 1
    features.sort(key=lambda pair: pair[0])
    warnings = []
    if skipped:
        warnings.append(f"{skipped} mapped features had incomplete or invalid geometry and were skipped")
    if len(features) > MAX_FEATURES:
        warnings.append(f"Mapped features were limited to {MAX_FEATURES} candidates inside the inspected areas")
        features = _limit_candidates(features, areas)
    return [feature for _, feature in features], warnings


def _limit_candidates(features, areas):
    # Round-robin across feature kinds and search neighborhoods so a dense
    # downtown block cannot crowd every other area out of the response.
    buckets = defaultdict(deque)
    for index, feature in features:
        nearest = min(range(len(areas)), key=lambda i:
            (feature["latitude"] - areas[i]["latitude"]) ** 2 +
            ((feature["longitude"] - areas[i]["longitude"]) * math.cos(math.radians(feature["latitude"]))) ** 2)
        buckets[(feature["kind"], nearest)].append((index, feature))
    selected = []
    ordered_buckets = [buckets[key] for key in sorted(buckets)]
    while len(selected) < MAX_FEATURES and any(ordered_buckets):
        for bucket in ordered_buckets:
            if bucket and len(selected) < MAX_FEATURES:
                selected.append(bucket.popleft())
    return sorted(selected, key=lambda pair: pair[0])


def _local_roads(areas, local_crs, forward, reverse, inspected, retrieved_at):
    context = environment.get_cached_context()
    roads = context.get("roads_gdf")
    if roads is None or roads.empty or roads.crs is None:
        return [], [], []
    # Query the existing index in its native CRS before projecting a small subset.
    to_native = Transformer.from_crs(local_crs, roads.crs, always_xy=True).transform
    native_search = transform(to_native, inspected)
    positions = roads.sindex.query(native_search, predicate="intersects")
    features, warnings = [], []
    to_wgs84 = Transformer.from_crs(roads.crs, "EPSG:4326", always_xy=True).transform
    for position in sorted(set(int(index) for index in positions))[:MAX_ELEMENTS]:
        row = roads.iloc[position]
        geometry = row.geometry
        if geometry is None or geometry.geom_type not in {"LineString", "MultiLineString"}:
            continue
        try:
            details = _clipped_geometry(transform(to_wgs84, geometry), "road", forward, reverse, inspected)
            if details is None:
                continue
            name = next((str(row[key])[:240] for key in ("STREETNAME", "ROADNAME", "STREET_NAM", "RD_NAME", "NAME") if key in row and isinstance(row[key], str) and row[key].strip()), "Mapped road segment")
            features.append({"id": f"massdot:road:{position}", "kind": "road", "name": name,
                "source": "MassDOT roads via MassGIS", "source_url": MASSDOT_URL,
                "tags": {}, **details})
        except (ValueError, TypeError, KeyError, GEOSException):
            continue
    if len(positions) > MAX_ELEMENTS:
        warnings.append(f"Local road retrieval was limited to {MAX_ELEMENTS} source segments")
    if len(features) > MAX_FEATURES:
        warnings.append(f"Local roads were limited to {MAX_FEATURES} candidates across the inspected areas")
        features = [feature for _, feature in _limit_candidates(list(enumerate(features)), areas)]
    source = {"name": "MassDOT roads via MassGIS", "url": MASSDOT_URL,
              "attribution": "Massachusetts Department of Transportation / MassGIS",
              "retrieved_at": retrieved_at, "data_timestamp": None,
              "coverage": "Cached local road segments within inspected areas; buildings and habitat footprints are unavailable"}
    return features, [source] if features else [], warnings


def discover_places(areas, latitude, longitude, radius_km, region):
    """Retrieve bounded, source-backed features without breaking habitat analysis.

    ``available`` means the mapping response was usable; coverage is still
    partial. ``partial`` flags invalid/limited geometry or a road-only fallback.
    No empty or unavailable response establishes that real features are absent.
    """
    now = time.time()
    result = {"status": "unavailable", "features": [], "sources": [], "warnings": [],
              "retrieved_at": _timestamp(now), "searched_areas": []}
    try:
        areas, local_crs, forward, reverse, inspected = _search_geometry(areas, latitude, longitude, radius_km)
    except (ValueError, TypeError, OverflowError):
        result["warnings"] = ["No usable map search neighborhoods were available"]
        return result
    result["searched_areas"] = areas
    result["warnings"].append(COVERAGE_WARNING)
    key = _cache_key(areas, float(latitude), float(longitude), float(radius_km))
    cached = _read_cache(key, now)
    try:
        payload, epoch = cached if cached is not None else (_fetch(areas), now)
        elements = _validate_payload(payload)
        features, geometry_warnings = _parse_features(elements, areas, forward, reverse, inspected)
        retrieved_at = _timestamp(epoch)
        metadata = payload.get("osm3s")
        data_timestamp = metadata.get("timestamp_osm_base") if isinstance(metadata, dict) else None
        result.update(status="partial" if geometry_warnings else "available", features=features,
                      retrieved_at=retrieved_at, sources=[{
                          "name": "OpenStreetMap via Overpass", "url": OSM_URL,
                          "attribution": "© OpenStreetMap contributors · Open Database License",
                          "retrieved_at": retrieved_at, "data_timestamp": str(data_timestamp)[:80] if data_timestamp else None,
                          "coverage": "Mapped features within the listed inspected neighborhoods"}])
        result["warnings"].extend(geometry_warnings)
        if cached is None:
            try:
                _write_cache(key, payload, epoch)
            except (OSError, ValueError, TypeError):
                pass
        return result
    except (requests.RequestException, ValueError, TypeError, KeyError, OverflowError):
        result["warnings"].append("Online building, road and habitat mapping was unavailable; existing habitat results remain usable")
    if str(region).upper() == "MA":
        try:
            features, sources, warnings = _local_roads(areas, local_crs, forward, reverse, inspected, result["retrieved_at"])
            if features:
                result.update(status="partial", features=features, sources=sources)
                result["warnings"].append("Using cached Massachusetts road segments; buildings and mapped habitat boundaries were not retrieved")
                result["warnings"].extend(warnings)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            result["warnings"].append("Cached Massachusetts road geometry was also unavailable")
    return result
