"""iNaturalist observations and target-group background sampling."""

import re
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from pyproj import Transformer

API_BASE = "https://api.inaturalist.org/v1"
DEFAULT_MAX_OBSERVATIONS = 5000
DEFAULT_MAX_BACKGROUND_POOL = 8000
MIN_BACKGROUND_POINTS = 25
OBSERVATION_COLUMNS = [
    "observation_id",
    "observed_on",
    "latitude",
    "longitude",
    "positional_accuracy",
    "quality_grade",
    "taxon_id",
    "taxon_name",
    "common_name",
    "coordinates_obscured",
    "uri",
]


def species_slug(species_name):
    slug = re.sub(r"[^a-z0-9]+", "_", species_name.strip().lower())
    slug = slug.strip("_")
    if not slug:
        raise ValueError("Species name is required.")
    return slug


def normalize_name(value):
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.strip().lower()).strip()


def api_get(endpoint, params=None):
    response = requests.get(f"{API_BASE}{endpoint}", params=params, timeout=60)
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    return data


def find_place_id(place_name):
    from wildlocate.core.regional import REGIONS
    for region in REGIONS.values():
        if str(place_name).casefold() in (region.name.casefold(), region.code.casefold()):
            return region.place_id
    place_name = place_name.strip()
    if not place_name:
        raise ValueError("Place name is required.")

    data = api_get("/places/autocomplete", {"q": place_name, "per_page": 20})
    results = data.get("results", [])
    if not results:
        raise RuntimeError(f"Could not find place: {place_name}")

    normalized_query = normalize_name(place_name)
    for result in results:
        if normalized_query in {
            normalize_name(result.get("display_name")),
            normalize_name(result.get("name")),
        }:
            return int(result["id"])

    return int(results[0]["id"])


def species_suggestions(query, place_name, limit=3):
    """Return up to limit in-region mammal/reptile species containing the query."""
    if query is None or not str(query).strip():
        raise ValueError("Species search is required.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
        raise ValueError("Suggestion limit must be between 1 and 10.")

    query = str(query).strip()
    normalized_query = normalize_name(query)
    place_id = find_place_id(place_name)

    # Autocomplete is useful for exact/common queries, while /taxa is broader
    # and finds species such as "White-tailed Deer" from the query "deer".
    search_results = []
    seen_ids = set()
    searches = (
        ("/taxa/autocomplete", {"q": query, "per_page": 100}),
        ("/taxa", {"q": query, "rank": "species", "per_page": 100}),
    )
    for endpoint, params in searches:
        data = api_get(endpoint, params)
        for taxon in data.get("results", []):
            taxon_id = taxon.get("id")
            if taxon_id is None or taxon_id in seen_ids:
                continue
            seen_ids.add(taxon_id)
            search_results.append(taxon)

    textual_matches = []
    for taxon in search_results:
        if taxon.get("rank") != "species":
            continue
        if taxon.get("iconic_taxon_name") not in {"Mammalia", "Reptilia"}:
            continue

        common_name = taxon.get("preferred_common_name") or taxon.get("common_name") or ""
        scientific_name = taxon.get("scientific_name") or taxon.get("name") or ""
        normalized_common = normalize_name(common_name)
        normalized_scientific = normalize_name(scientific_name)
        if normalized_query not in normalized_common and normalized_query not in normalized_scientific:
            continue
        if not common_name or not scientific_name:
            continue

        # Prefer common-name matches, then shorter names; state abundance is
        # applied after we verify that the species actually occurs in the region.
        text_priority = (
            0 if normalized_common == normalized_query else
            1 if normalized_common.startswith(normalized_query) else
            2 if normalized_query in normalized_common else
            3
        )
        textual_matches.append((text_priority, len(normalized_common), taxon))

    textual_matches.sort(key=lambda item: (item[0], item[1]))
    candidates = []
    for text_priority, _name_length, taxon in textual_matches[:30]:
        observation_data = api_get(
            "/observations",
            {
                "taxon_id": int(taxon["id"]),
                "place_id": place_id,
                "quality_grade": "research",
                "verifiable": "true",
                "captive": "false",
                "per_page": 1,
            },
        )
        observation_count = int(observation_data.get("total_results", 0) or 0)
        if observation_count <= 0:
            continue

        common_name = taxon.get("preferred_common_name") or taxon.get("common_name") or ""
        scientific_name = taxon.get("scientific_name") or taxon.get("name") or ""
        candidates.append(
            {
                "taxon_id": int(taxon["id"]),
                "scientific_name": scientific_name,
                "common_name": common_name,
                "rank": "species",
                "iconic_taxon_name": taxon.get("iconic_taxon_name"),
                "observation_count": observation_count,
                "_text_priority": text_priority,
            }
        )

    candidates.sort(
        key=lambda item: (
            item["_text_priority"],
            -item["observation_count"],
            len(normalize_name(item["common_name"])),
        )
    )
    for item in candidates:
        item.pop("_text_priority", None)
    return candidates[:limit]

def resolve_species(species_name):
    if species_name is None or not species_name.strip():
        raise ValueError("Species name is required.")

    normalized_query = normalize_name(species_name)
    data = api_get("/taxa/autocomplete", {"q": species_name, "per_page": 20})
    results = data.get("results", [])

    if not results:
        raise ValueError(
            f"Could not find a species match for '{species_name}'. Please provide a clearer common or scientific name."
        )

    exact_matches = []
    for taxon in results:
        taxon_names = [
            taxon.get("preferred_common_name"),
            taxon.get("common_name"),
            taxon.get("scientific_name"),
            taxon.get("name"),
        ]
        if any(normalize_name(name) == normalized_query for name in taxon_names if name):
            exact_matches.append(taxon)

    if not exact_matches:
        raise ValueError(
            f"Could not find a reasonable species match for '{species_name}'. "
            f"Try a more exact common or scientific name; available matches include: "
            f"{', '.join(sorted({(item.get('preferred_common_name') or item.get('name') or 'Unknown') for item in results[:5]}))}."
        )

    chosen = exact_matches[0]
    common_name = chosen.get("preferred_common_name") or chosen.get("common_name") or ""
    scientific_name = chosen.get("scientific_name") or chosen.get("name") or ""

    # Autocomplete can return a sparse exact match when queried by scientific
    # name. Fetch the canonical taxon record before rejecting a suggestion that
    # was already shown to the user with a valid common name.
    if not common_name or not scientific_name:
        detail = api_get(f"/taxa/{int(chosen['id'])}")
        detailed = (detail.get("results") or [{}])[0]
        common_name = (
            common_name
            or detailed.get("preferred_common_name")
            or detailed.get("common_name")
            or ""
        )
        scientific_name = (
            scientific_name
            or detailed.get("scientific_name")
            or detailed.get("name")
            or ""
        )
        chosen = {**chosen, **detailed}

    if not common_name or not scientific_name:
        raise ValueError(
            f"Could not resolve a usable common and scientific name for '{species_name}'."
        )

    return {
        "taxon_id": int(chosen["id"]),
        "scientific_name": scientific_name,
        "common_name": common_name,
        "rank": chosen.get("rank"),
        "iconic_taxon_name": chosen.get("iconic_taxon_name"),
    }


def extract_observation_row(obs):
    geojson = obs.get("geojson") or {}
    coordinates = geojson.get("coordinates")
    if not coordinates or len(coordinates) < 2:
        return None

    longitude, latitude = coordinates[:2]
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return None

    if not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
        return None

    taxon = obs.get("taxon") or {}
    common_name = taxon.get("preferred_common_name") or taxon.get("common_name") or ""
    taxon_name = taxon.get("scientific_name") or taxon.get("name") or ""

    return {
        "observation_id": obs.get("id"),
        "observed_on": obs.get("observed_on"),
        "latitude": latitude,
        "longitude": longitude,
        "positional_accuracy": obs.get("positional_accuracy"),
        "quality_grade": obs.get("quality_grade"),
        "taxon_id": taxon.get("id"),
        "taxon_name": taxon_name,
        "common_name": common_name,
        "coordinates_obscured": bool(obs.get("obscured", False)),
        "uri": obs.get("uri"),
    }


def download_species_observations(
    species_name,
    place_name="Massachusetts",
    max_observations=DEFAULT_MAX_OBSERVATIONS,
    *, taxon_id=None, progress=None,
):
    taxon_id = taxon_id if taxon_id is not None else resolve_species(species_name)["taxon_id"]
    place_id = find_place_id(place_name)

    if max_observations <= 0:
        raise ValueError("max_observations must be greater than 0.")

    rows = []
    id_above = 0

    while len(rows) < max_observations:
        params = {
            "taxon_id": taxon_id,
            "place_id": place_id,
            "quality_grade": "research",
            "verifiable": "true",
            "captive": "false",
            "per_page": 200,
            "order_by": "id",
            "order": "asc",
            "id_above": id_above,
        }

        data = api_get("/observations", params)
        batch = data.get("results", [])
        if not batch:
            break

        for obs in batch:
            if len(rows) >= max_observations:
                break
            row = extract_observation_row(obs)
            if row is not None:
                rows.append(row)

        if len(batch) < params["per_page"]:
            break

        id_above = batch[-1]["id"]
        if progress:
            progress(f"Downloaded {len(rows):,} observations…")

    if not rows:
        raise RuntimeError(
            f"No research-grade, non-captive observations were found for {species_name} in {place_name}."
        )

    raw_df = pd.DataFrame(rows, columns=OBSERVATION_COLUMNS)
    return raw_df


def clean_species_observations(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=OBSERVATION_COLUMNS)

    cleaned = df.copy()

    cleaned = cleaned.dropna(subset=["latitude", "longitude"]).copy()

    cleaned["latitude"] = pd.to_numeric(cleaned["latitude"], errors="coerce")
    cleaned["longitude"] = pd.to_numeric(cleaned["longitude"], errors="coerce")
    cleaned = cleaned.dropna(subset=["latitude", "longitude"]).copy()

    cleaned["coordinates_obscured"] = (
        cleaned["coordinates_obscured"].fillna(False).astype(str).str.lower() == "true"
    )
    cleaned = cleaned[~cleaned["coordinates_obscured"]].copy()

    cleaned["positional_accuracy"] = pd.to_numeric(cleaned["positional_accuracy"], errors="coerce")
    cleaned = cleaned[
        cleaned["positional_accuracy"].isna() | (cleaned["positional_accuracy"] <= 250)
    ].copy()

    cleaned = cleaned.drop_duplicates(subset=["observation_id"]).copy()
    cleaned = cleaned.drop_duplicates(subset=["latitude", "longitude"]).copy()

    cleaned = cleaned.loc[:, OBSERVATION_COLUMNS].reset_index(drop=True)
    return cleaned


def save_cleaned_observations(df, species_name, output_dir="data/processed/samples"):
    output_path = Path(output_dir) / f"{species_slug(species_name)}_occurrences.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    return output_path



TARGET_GROUPS = {
    "Mammalia": {"taxon_id": 40151, "label": "mammal"},
    "Reptilia": {"taxon_id": 26036, "label": "reptile"},
}
BACKGROUND_POOL_COLUMNS = (
    "observation_id", "taxon_id", "taxon_name", "common_name",
    "latitude", "longitude", "positional_accuracy", "observed_on",
    "coordinates_obscured",
)


def target_group_label(target_group):
    try:
        return TARGET_GROUPS[target_group]["label"]
    except KeyError as exc:
        raise ValueError(f"Unsupported target group: {target_group}") from exc


def download_target_group_pool(
    target_group, place_name="Massachusetts", progress=None,
    max_observations=DEFAULT_MAX_BACKGROUND_POOL,
):
    group = TARGET_GROUPS.get(target_group)
    if group is None:
        raise ValueError(f"Unsupported target group: {target_group}")
    if isinstance(max_observations, bool) or not isinstance(max_observations, int) or max_observations <= 0:
        raise ValueError("max_observations must be a positive integer.")
    max_observations = min(max_observations, DEFAULT_MAX_BACKGROUND_POOL)

    place_id = find_place_id(place_name)
    rows = []
    id_above = 0

    while len(rows) < max_observations:
        params = {
            "taxon_id": group["taxon_id"],
            "place_id": place_id,
            "quality_grade": "research",
            "verifiable": "true",
            "captive": "false",
            "per_page": 200,
            "order_by": "id",
            "order": "asc",
            "id_above": id_above,
        }
        data = api_get("/observations", params)
        batch = data.get("results", [])
        if not batch:
            break

        for obs in batch:
            row = extract_observation_row(obs)
            if row is not None:
                rows.append({key: row[key] for key in BACKGROUND_POOL_COLUMNS})
                if len(rows) >= max_observations:
                    break

        if len(batch) < params["per_page"]:
            break
        id_above = batch[-1]["id"]
        if progress:
            progress(
                f"Downloaded {len(rows):,} {place_name} "
                f"{group['label']} background observations…"
            )

    if not rows:
        raise RuntimeError(
            f"No usable {place_name} {group['label']} observations were found."
        )

    df = pd.DataFrame(rows)
    for column in ("observation_id", "taxon_id", "positional_accuracy"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def load_or_create_target_group_pool(
    target_group, refresh_pool=False, pool_file=None, progress=None,
    place_name="Massachusetts",
):
    label = target_group_label(target_group)
    if pool_file is None:
        place_slug = re.sub(r"[^a-z0-9]+", "_", place_name.casefold()).strip("_")
        pool_file = Path("data/processed/samples") / f"{place_slug}_{label}_pool.csv"
    else:
        pool_file = Path(pool_file)

    if pool_file.exists() and not refresh_pool:
        pool_df = pd.read_csv(pool_file)
        missing = set(BACKGROUND_POOL_COLUMNS).difference(pool_df.columns)
        if missing:
            raise ValueError(
                f"Existing {label} pool at {pool_file} is missing required columns: "
                f"{sorted(missing)}"
            )
        if len(pool_df) > DEFAULT_MAX_BACKGROUND_POOL:
            pool_df = pool_df.sample(
                n=DEFAULT_MAX_BACKGROUND_POOL, random_state=42
            ).reset_index(drop=True)
            pool_df.to_csv(pool_file, index=False)
        return pool_df

    pool_df = download_target_group_pool(
        target_group, place_name=place_name, progress=progress
    )
    pool_file.parent.mkdir(parents=True, exist_ok=True)
    pool_df.to_csv(pool_file, index=False)
    return pool_df

def project_to_5070(df):
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    x_5070, y_5070 = transformer.transform(df["longitude"].to_numpy(), df["latitude"].to_numpy())

    projected = df.copy()
    projected["x_5070"] = x_5070
    projected["y_5070"] = y_5070
    return projected


def filter_target_group_pool(pool_df, target_taxon_id):
    if pool_df is None or pool_df.empty:
        return pool_df

    filtered = pool_df.copy()

    filtered = filtered.dropna(subset=["latitude", "longitude"]).copy()
    filtered["latitude"] = pd.to_numeric(filtered["latitude"], errors="coerce")
    filtered["longitude"] = pd.to_numeric(filtered["longitude"], errors="coerce")
    filtered = filtered.dropna(subset=["latitude", "longitude"]).copy()

    filtered["coordinates_obscured"] = (
        filtered["coordinates_obscured"].fillna(False).astype(str).str.lower() == "true"
    )
    filtered = filtered[~filtered["coordinates_obscured"]].copy()

    filtered["positional_accuracy"] = pd.to_numeric(filtered["positional_accuracy"], errors="coerce")
    filtered = filtered[
        filtered["positional_accuracy"].isna() | (filtered["positional_accuracy"] <= 250)
    ].copy()

    filtered = filtered.drop_duplicates(subset=["observation_id"]).copy()
    filtered = filtered.drop_duplicates(subset=["latitude", "longitude"]).copy()

    if target_taxon_id is not None:
        filtered = filtered[filtered["taxon_id"] != target_taxon_id].copy()

    return filtered


def compute_min_distance_to_presence(candidate_x, candidate_y, presence_x, presence_y):
    distances = np.full(candidate_x.shape[0], np.inf, dtype=float)

    for idx, (cx, cy) in enumerate(zip(candidate_x, candidate_y)):
        diff_x = presence_x - cx
        diff_y = presence_y - cy
        distances[idx] = np.sqrt(np.min(diff_x**2 + diff_y**2))

    return distances


def spatial_thin(df, thinning_distance_m, random_state):
    if df.empty:
        return df

    projected = project_to_5070(df)
    projected["grid_x"] = np.floor(projected["x_5070"] / thinning_distance_m).astype(int)
    projected["grid_y"] = np.floor(projected["y_5070"] / thinning_distance_m).astype(int)

    rng = np.random.default_rng(random_state)
    order = rng.permutation(len(projected))
    kept = []
    seen_cells = set()

    for idx in order:
        row = projected.iloc[idx]
        cell = (int(row["grid_x"]), int(row["grid_y"]))
        if cell in seen_cells:
            continue
        seen_cells.add(cell)
        kept.append(idx)

    return projected.iloc[kept].reset_index(drop=True)


def generate_background(
    species_name,
    background_ratio=1.0,
    exclusion_distance_m=1000,
    thinning_distance_m=500,
    random_state=42,
    *, samples_dir="data/processed/samples", pool_file=None, taxon_id=None, progress=None,
    place_name="Massachusetts", target_group=None,
):
    if background_ratio <= 0:
        raise ValueError("background_ratio must be greater than 0.")
    if exclusion_distance_m < 0:
        raise ValueError("exclusion_distance_m must be non-negative.")
    if thinning_distance_m <= 0:
        raise ValueError("thinning_distance_m must be greater than 0.")

    resolved = None
    if taxon_id is None or target_group is None:
        resolved = resolve_species(species_name)
    target_taxon_id = int(taxon_id if taxon_id is not None else resolved["taxon_id"])
    target_group = target_group or resolved["iconic_taxon_name"]
    group_label = target_group_label(target_group)

    species_slug_value = species_slug(species_name)
    samples_dir = Path(samples_dir)
    presence_file = samples_dir / f"{species_slug_value}_occurrences.csv"
    if not presence_file.exists():
        raise FileNotFoundError(
            f"Could not find cleaned presence file at {presence_file}. Run the Phase 3 species download first."
        )

    presence_df = pd.read_csv(presence_file)
    presence_df = presence_df.dropna(subset=["latitude", "longitude"]).copy()
    presence_df["latitude"] = pd.to_numeric(presence_df["latitude"], errors="coerce")
    presence_df["longitude"] = pd.to_numeric(presence_df["longitude"], errors="coerce")
    presence_df = presence_df.dropna(subset=["latitude", "longitude"]).copy()

    if presence_df.empty:
        raise RuntimeError(f"No usable presence points found in {presence_file}.")

    pool_df = load_or_create_target_group_pool(
        target_group, pool_file=pool_file, progress=progress, place_name=place_name
    )
    candidate_df = filter_target_group_pool(pool_df, target_taxon_id)

    if candidate_df.empty:
        raise RuntimeError(
            f"No usable {group_label}-pool candidates remain after quality filtering for {species_name}."
        )

    projected_presence = project_to_5070(presence_df)
    projected_candidates = project_to_5070(candidate_df)

    presence_x = projected_presence["x_5070"].to_numpy(dtype=float)
    presence_y = projected_presence["y_5070"].to_numpy(dtype=float)
    candidate_x = projected_candidates["x_5070"].to_numpy(dtype=float)
    candidate_y = projected_candidates["y_5070"].to_numpy(dtype=float)

    min_distances = compute_min_distance_to_presence(candidate_x, candidate_y, presence_x, presence_y)
    projected_candidates["distance_to_presence_m"] = min_distances

    projected_candidates = projected_candidates[
        projected_candidates["distance_to_presence_m"] >= exclusion_distance_m
    ].copy()

    if projected_candidates.empty:
        raise RuntimeError(
            f"No candidate background points remain after excluding all locations within {exclusion_distance_m} m of known {species_name} presences."
        )

    thinned_candidates = spatial_thin(
        projected_candidates[["latitude", "longitude", "observation_id", "taxon_id", "taxon_name", "common_name"]].copy(),
        thinning_distance_m,
        random_state,
    )

    desired_background = max(
        MIN_BACKGROUND_POINTS,
        int(round(len(presence_df) * background_ratio)),
    )
    if len(thinned_candidates) < MIN_BACKGROUND_POINTS:
        raise RuntimeError(
            f"Too few candidate background points remain after filtering and thinning: "
            f"{len(thinned_candidates)} available, at least {MIN_BACKGROUND_POINTS} required."
        )

    # Do not fail just because the target-group pool cannot supply a full 1:1
    # sample. Use every valid thinned candidate instead.
    required_background = min(desired_background, len(thinned_candidates))

    rng = np.random.default_rng(random_state)
    sample_indices = rng.choice(len(thinned_candidates), size=required_background, replace=False)
    sample_df = thinned_candidates.iloc[sample_indices].reset_index(drop=True)

    sample_df = sample_df[["latitude", "longitude"]].copy()
    sample_df["presence"] = 0

    background_file = samples_dir / f"{species_slug_value}_background.csv"
    background_file.parent.mkdir(parents=True, exist_ok=True)
    sample_df.to_csv(background_file, index=False)

    training_df = pd.concat(
        [
            presence_df[["latitude", "longitude"]].copy().assign(presence=1),
            sample_df[["latitude", "longitude", "presence"]].copy(),
        ],
        ignore_index=True,
    )

    training_df = training_df.sample(frac=1, random_state=random_state).reset_index(drop=True)

    training_file = samples_dir / f"{species_slug_value}_training_points.csv"
    training_file.parent.mkdir(parents=True, exist_ok=True)
    training_df.to_csv(training_file, index=False)

    return sample_df
