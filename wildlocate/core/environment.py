"""Environmental data paths, downloads, and feature extraction."""


import math
import os
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import platformdirs
import rasterio
import requests
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.merge import merge
from rasterio.windows import Window
from rasterio.warp import calculate_default_transform, reproject
from shapely.geometry import Point
def get_user_data_dir() -> Path:
    """Directory where downloaded environmental datasets are stored."""
    override = os.environ.get("WILDLOCATE_DATA_DIR")
    return Path(override).expanduser().resolve() if override else Path(platformdirs.user_data_dir("wildlocate"))


class DatasetPaths:
    def __init__(self, root=None):
        root = Path(root) if root is not None else get_user_data_dir()

        self.nlcd_landcover = root / "raw" / "nlcd" / "landcover_2025.tif"
        self.nlcd_impervious = root / "raw" / "nlcd" / "impervious_2025.tif"
        self.usgs_3dep_elevation = root / "raw" / "usgs_3dep" / "elevation_3dep.tif"
        self.massdep_hydrography_poly = root / "raw" / "massdep_hydrography" / "HYDRO25K_POLY.shp"
        self.massdep_hydrography_arc = root / "raw" / "massdep_hydrography" / "HYDRO25K_ARC.shp"
        self.massdot_roads = root / "raw" / "massdot_roads" / "EOTROADS_ARC.shp"

    def validate(self):
        resolved = {
            "NLCD land cover": self.nlcd_landcover,
            "NLCD impervious surface": self.nlcd_impervious,
            "USGS 3DEP elevation": self.usgs_3dep_elevation,
            "MassDEP hydrography POLY": self.massdep_hydrography_poly,
            "MassDEP hydrography ARC": self.massdep_hydrography_arc,
            "MassDOT roads": self.massdot_roads,
        }

        missing = []
        for label, path in resolved.items():
            if not path.exists():
                missing.append(f"{label}: {path}")

        if missing:
            missing_text = "\n".join(missing)
            raise FileNotFoundError(
                "Missing required raw environmental datasets. Run 'wildlocate init' to download them.\n"
                + missing_text
            )

        return resolved


ENVIRONMENT_PATHS = DatasetPaths()




LANDCOVER_WORKSPACE = "mrlc_Land-Cover-Native_conus_year_data"
IMPERVIOUS_WORKSPACE = "mrlc_Fractional-Impervious-Surface-Native_conus_year_data"
BASE = "https://dmsdata.cr.usgs.gov/geoserver"
LANDCOVER_WCS = f"{BASE}/{LANDCOVER_WORKSPACE}/wcs"
IMPERVIOUS_WCS = f"{BASE}/{IMPERVIOUS_WORKSPACE}/wcs"
LANDCOVER_COVERAGE = f"{LANDCOVER_WORKSPACE}:Land-Cover-Native_conus_year_data"
IMPERVIOUS_COVERAGE = f"{IMPERVIOUS_WORKSPACE}:Fractional-Impervious-Surface-Native_conus_year_data"
MASS_BBOX_WGS84 = (-73.60, 41.10, -69.80, 42.95)
NLCD_CRS = "EPSG:5070"
NLCD_RESOLUTION_M = 30
TIMEOUT = 240
ELEVATION_URL = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
HYDROGRAPHY_URL = "https://s3.us-east-1.amazonaws.com/download.massgis.digital.mass.gov/shapefiles/state/hydro25k.zip"
ROADS_URL = "https://s3.us-east-1.amazonaws.com/download.massgis.digital.mass.gov/shapefiles/state/MassDOT_Roads_SHP.zip"


def mass_bbox_5070():
    xmin, ymin, xmax, ymax = MASS_BBOX_WGS84
    transformer = Transformer.from_crs("EPSG:4326", NLCD_CRS, always_xy=True)
    corners = [
        transformer.transform(xmin, ymin),
        transformer.transform(xmin, ymax),
        transformer.transform(xmax, ymin),
        transformer.transform(xmax, ymax),
    ]
    xs, ys = zip(*corners)
    return min(xs), min(ys), max(xs), max(ys)


def tile_bbox(bbox, tiles_x, tiles_y):
    xmin, ymin, xmax, ymax = bbox
    dx, dy = (xmax - xmin) / tiles_x, (ymax - ymin) / tiles_y
    for row in range(tiles_y):
        for col in range(tiles_x):
            yield row, col, (
                xmin + col * dx,
                ymin + row * dy,
                xmin + (col + 1) * dx,
                ymin + (row + 1) * dy,
            )


def download_tile(wcs_url, coverage, bbox, year, output_path):
    xmin, ymin, xmax, ymax = bbox
    params = {
        "service": "WCS", "version": "1.0.0", "request": "GetCoverage",
        "coverage": coverage, "CRS": NLCD_CRS,
        "BBOX": f"{xmin},{ymin},{xmax},{ymax}",
        "time": f"{year}-01-01T00:00:00.000Z",
        "format": "image/geotiff", "resx": NLCD_RESOLUTION_M, "resy": NLCD_RESOLUTION_M,
    }
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(wcs_url, params=params, stream=True, timeout=TIMEOUT) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if "xml" in content_type or "text" in content_type:
            raise RuntimeError("MRLC returned an error instead of GeoTIFF:\n" + response.text[:4000])
        with output_path.open("wb") as stream:
            for chunk in response.iter_content(1024 * 1024):
                if chunk:
                    stream.write(chunk)
    try:
        with rasterio.open(output_path) as src:
            if src.count < 1:
                raise RuntimeError("Raster has zero bands.")
    except Exception as exc:
        raise RuntimeError(f"Downloaded file is not a valid GeoTIFF: {output_path}") from exc


def _mosaic(tile_paths, output_path):
    datasets = [rasterio.open(path) for path in tile_paths]
    try:
        mosaic, transform = merge(datasets)
        profile = datasets[0].profile.copy()
        profile.update(
            height=mosaic.shape[1], width=mosaic.shape[2], transform=transform,
            compress="deflate", tiled=True, BIGTIFF="IF_SAFER",
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(output_path, "w", **profile) as dst:
            dst.write(mosaic)
    finally:
        for dataset in datasets:
            dataset.close()


def _download_nlcd_product(wcs_url, coverage, year, output_path, bbox, tiles):
    temporary_dir = Path(tempfile.mkdtemp(prefix="wild_locate_nlcd_"))
    paths = []
    try:
        for row, col, tile in tile_bbox(bbox, tiles, tiles):
            path = temporary_dir / f"tile_{row}_{col}.tif"
            download_tile(wcs_url, coverage, tile, year, path)
            paths.append(path)
        _mosaic(paths, Path(output_path))
    finally:
        shutil.rmtree(temporary_dir, ignore_errors=True)


def download_nlcd(output_dir, year=2025, tiles=4):
    if not 1985 <= year <= 2025:
        raise ValueError("Annual NLCD currently covers 1985-2025.")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    bbox = mass_bbox_5070()
    _download_nlcd_product(
        LANDCOVER_WCS, LANDCOVER_COVERAGE, year,
        output_dir / f"landcover_{year}.tif", bbox, tiles,
    )
    _download_nlcd_product(
        IMPERVIOUS_WCS, IMPERVIOUS_COVERAGE, year,
        output_dir / f"impervious_{year}.tif", bbox, tiles,
    )


def download_elevation(bbox, output_path, width=2400, height=2400):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    params = {
        "bbox": ",".join(str(value) for value in bbox), "bboxSR": 4326,
        "size": f"{width},{height}", "imageSR": 4326, "format": "tiff",
        "pixelType": "F32", "noData": "-9999", "interpolation": "NearestNeighbor",
        "f": "image",
    }
    with requests.get(ELEVATION_URL, params=params, timeout=TIMEOUT, stream=True) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if "xml" in content_type or "text" in content_type:
            raise RuntimeError("The 3DEP service returned an error instead of a GeoTIFF:\n" + response.text[:4000])
        with output_path.open("wb") as stream:
            for chunk in response.iter_content(1024 * 1024):
                if chunk:
                    stream.write(chunk)
    with rasterio.open(output_path):
        pass


def _download_zip(url, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, timeout=TIMEOUT) as response:
        response.raise_for_status()
        output_path.write_bytes(response.content)
    with zipfile.ZipFile(output_path) as archive:
        archive.extractall(output_path.parent)


def download_hydrography(output_path):
    _download_zip(HYDROGRAPHY_URL, output_path)


def download_roads(output_path):
    _download_zip(ROADS_URL, output_path)




VALID_CLASSES = {
    "forest": {41, 42, 43},
    "wetland": {90, 95},
    "developed": {21, 22, 23, 24},
    "open_water": {11},
}

FEATURE_ORDER = [
    "forest_fraction_250m",
    "forest_fraction_1000m",
    "wetland_fraction_250m",
    "wetland_fraction_1000m",
    "developed_fraction_250m",
    "developed_fraction_1000m",
    "open_water_fraction_250m",
    "open_water_fraction_1000m",
    "mean_impervious_250m",
    "mean_impervious_1000m",
    "elevation_m",
    "mean_slope_250m",
    "mean_slope_1000m",
    "terrain_ruggedness_1000m",
    "distance_to_water_m",
    "distance_to_road_m",
]

_CONTEXT_CACHE = None


def ensure_projected_elevation_raster(root=None):
    root = Path(root) if root is not None else get_user_data_dir()
    source_path = root / "raw" / "usgs_3dep" / "elevation_3dep.tif"
    target_path = root / "raw" / "usgs_3dep" / "elevation_3dep_5070.tif"

    if target_path.exists():
        return target_path

    if not source_path.exists():
        raise FileNotFoundError(
            f"Could not find source elevation raster at {source_path}."
        )

    temporary_path = target_path.with_name(f".{target_path.stem}.{uuid.uuid4().hex}.tif")
    try:
        with rasterio.open(source_path) as src:
            transform, width, height = calculate_default_transform(
                src.crs,
                "EPSG:5070",
                src.width,
                src.height,
                *src.bounds,
            )

            kwargs = src.meta.copy()
            kwargs.update(
                {
                    "crs": "EPSG:5070",
                    "transform": transform,
                    "width": width,
                    "height": height,
                }
            )

            with rasterio.open(temporary_path, "w", **kwargs) as dst:
                for band_index in range(1, src.count + 1):
                    reproject(
                        source=rasterio.band(src, band_index),
                        destination=rasterio.band(dst, band_index),
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=transform,
                        dst_crs="EPSG:5070",
                        resampling=Resampling.bilinear,
                    )

        os.replace(temporary_path, target_path)
    finally:
        temporary_path.unlink(missing_ok=True)

    return target_path



def get_paths():
    return ENVIRONMENT_PATHS.validate()


def get_cached_context():
    global _CONTEXT_CACHE

    if _CONTEXT_CACHE is not None:
        return _CONTEXT_CACHE

    paths = get_paths()
    elevation_path = ensure_projected_elevation_raster()

    landcover_src = rasterio.open(paths["NLCD land cover"])
    impervious_src = rasterio.open(paths["NLCD impervious surface"])
    elevation_src = rasterio.open(elevation_path)

    hydro_poly_gdf = gpd.read_file(paths["MassDEP hydrography POLY"]).to_crs("EPSG:5070")
    hydro_arc_gdf = gpd.read_file(paths["MassDEP hydrography ARC"]).to_crs("EPSG:5070")
    hydro_combined_gdf = gpd.GeoDataFrame(
        geometry=pd.concat(
            [hydro_poly_gdf.geometry.copy(), hydro_arc_gdf.geometry.copy()],
            ignore_index=True,
        ),
        crs="EPSG:5070",
    )
    roads_gdf = gpd.read_file(paths["MassDOT roads"]).to_crs("EPSG:5070")

    _CONTEXT_CACHE = {
        "landcover_src": landcover_src,
        "impervious_src": impervious_src,
        "elevation_src": elevation_src,
        "hydro_combined_gdf": hydro_combined_gdf,
        "roads_gdf": roads_gdf,
    }

    return _CONTEXT_CACHE


def project_point(latitude, longitude):
    if not (-90 <= latitude <= 90):
        raise ValueError(f"Invalid latitude {latitude}. Latitude must be between -90 and 90.")
    if not (-180 <= longitude <= 180):
        raise ValueError(f"Invalid longitude {longitude}. Longitude must be between -180 and 180.")

    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    x_5070, y_5070 = transformer.transform(longitude, latitude)
    return float(x_5070), float(y_5070)


def validate_point_is_evaluable(
    x_5070,
    y_5070,
    src,
    label,
):
    bounds = src.bounds

    if x_5070 < bounds.left or x_5070 > bounds.right or y_5070 < bounds.bottom or y_5070 > bounds.top:
        raise ValueError(
            f"Requested coordinate cannot be evaluated for {label}: ({x_5070}, {y_5070}) is outside the raster bounds."
        )

    try:
        row_idx, col_idx = src.index(x_5070, y_5070)
    except Exception as exc:
        raise ValueError(
            f"Requested coordinate cannot be evaluated for {label}: unable to map ({x_5070}, {y_5070}) to raster indices."
        ) from exc

    if row_idx < 0 or row_idx >= src.height or col_idx < 0 or col_idx >= src.width:
        raise ValueError(
            f"Requested coordinate cannot be evaluated for {label}: mapped raster indices are out of bounds."
        )

    return row_idx, col_idx


def read_local_window(
    src,
    x_5070,
    y_5070,
    radius_m,
):
    row_idx, col_idx = validate_point_is_evaluable(x_5070, y_5070, src, src.name)

    pixel_size_x = abs(src.res[0])
    pixel_size_y = abs(src.res[1])
    radius_pixels = max(1, int(math.ceil(radius_m / max(pixel_size_x, pixel_size_y))))

    row_start = max(0, row_idx - radius_pixels)
    row_end = min(src.height, row_idx + radius_pixels + 1)
    col_start = max(0, col_idx - radius_pixels)
    col_end = min(src.width, col_idx + radius_pixels + 1)

    window = Window(col_start, row_start, col_end - col_start, row_end - row_start)
    data = src.read(1, window=window).astype(float)

    if src.nodata is not None:
        data[data == src.nodata] = np.nan

    local_row = row_idx - row_start
    local_col = col_idx - col_start

    y_idx, x_idx = np.mgrid[: data.shape[0], : data.shape[1]]
    distance_m = np.hypot(
        (y_idx - local_row) * max(pixel_size_x, pixel_size_y),
        (x_idx - local_col) * max(pixel_size_x, pixel_size_y),
    )

    valid_mask = np.isfinite(data) & (distance_m <= radius_m)

    return data, valid_mask, local_row, local_col, pixel_size_x, pixel_size_y


def fraction(values, class_ids):
    if values.size == 0:
        return float("nan")
    return float(np.mean(np.isin(values, list(class_ids))))


def terrain_window_stats(
    src,
    x_5070,
    y_5070,
    radius_m,
):
    data, valid_mask, local_row, local_col, pixel_size_x, pixel_size_y = read_local_window(
        src, x_5070, y_5070, radius_m
    )

    if np.count_nonzero(valid_mask) == 0:
        return float("nan"), float("nan"), float("nan")

    sample = data[valid_mask]
    elevation_m = float(np.nanmean(sample))

    ruggedness = float(np.std(sample, ddof=0))

    y_idx, x_idx = np.mgrid[: data.shape[0], : data.shape[1]]
    distance_m = np.hypot(
        (y_idx - local_row) * pixel_size_y,
        (x_idx - local_col) * pixel_size_x,
    )

    valid_slope_mask = np.isfinite(data) & (distance_m <= radius_m)
    if np.count_nonzero(valid_slope_mask) == 0:
        mean_slope = float("nan")
    else:
        y_grad = np.gradient(data, axis=0)
        x_grad = np.gradient(data, axis=1)
        slope_magnitudes = np.hypot(y_grad / pixel_size_y, x_grad / pixel_size_x)
        slope_magnitudes = np.degrees(np.arctan(slope_magnitudes))
        slope_values = slope_magnitudes[valid_slope_mask]
        mean_slope = float(np.nanmean(slope_values))

    return elevation_m, mean_slope, ruggedness


def nearest_distance_m(geodata, point):
    if geodata.empty:
        return float("nan")

    try:
        nearest_indices = np.asarray(geodata.sindex.nearest(point)).reshape(-1)
    except Exception:
        return float("nan")

    if nearest_indices.size == 0:
        return float("nan")

    nearest_distances = geodata.iloc[nearest_indices].distance(point)
    return float(nearest_distances.min())


def extract_features(latitude, longitude):
    context = get_cached_context()

    x_5070, y_5070 = project_point(latitude, longitude)

    landcover_src = context["landcover_src"]
    impervious_src = context["impervious_src"]
    elevation_src = context["elevation_src"]
    hydro_combined_gdf = context["hydro_combined_gdf"]
    roads_gdf = context["roads_gdf"]

    validate_point_is_evaluable(x_5070, y_5070, landcover_src, "NLCD land cover")
    validate_point_is_evaluable(x_5070, y_5070, impervious_src, "NLCD impervious surface")
    validate_point_is_evaluable(x_5070, y_5070, elevation_src, "USGS 3DEP elevation")

    point = Point(x_5070, y_5070)

    features = {}

    for radius_m in (250, 1000):
        lc_data, lc_valid_mask, _, _, _, _ = read_local_window(landcover_src, x_5070, y_5070, radius_m)
        imp_data, imp_valid_mask, _, _, _, _ = read_local_window(impervious_src, x_5070, y_5070, radius_m)

        lc_values = lc_data[lc_valid_mask]
        imp_values = imp_data[imp_valid_mask]

        if lc_values.size == 0:
            features[f"forest_fraction_{radius_m}m"] = float("nan")
            features[f"wetland_fraction_{radius_m}m"] = float("nan")
            features[f"developed_fraction_{radius_m}m"] = float("nan")
            features[f"open_water_fraction_{radius_m}m"] = float("nan")
        else:
            features[f"forest_fraction_{radius_m}m"] = fraction(lc_values, VALID_CLASSES["forest"])
            features[f"wetland_fraction_{radius_m}m"] = fraction(lc_values, VALID_CLASSES["wetland"])
            features[f"developed_fraction_{radius_m}m"] = fraction(lc_values, VALID_CLASSES["developed"])
            features[f"open_water_fraction_{radius_m}m"] = fraction(lc_values, VALID_CLASSES["open_water"])

        if imp_values.size == 0:
            features[f"mean_impervious_{radius_m}m"] = float("nan")
        else:
            features[f"mean_impervious_{radius_m}m"] = float(np.nanmean(imp_values))

    row_idx, col_idx = validate_point_is_evaluable(x_5070, y_5070, elevation_src, "USGS 3DEP elevation")
    elevation_value = elevation_src.read(1, window=Window(col_idx, row_idx, 1, 1))[0, 0]

    if elevation_src.nodata is not None and elevation_value == elevation_src.nodata:
        raise ValueError(
            f"Requested coordinate cannot be evaluated for USGS 3DEP elevation: DEM value is nodata at ({latitude}, {longitude})."
        )
    if np.isnan(elevation_value):
        raise ValueError(
            f"Requested coordinate cannot be evaluated for USGS 3DEP elevation: DEM value is invalid at ({latitude}, {longitude})."
        )

    features["elevation_m"] = float(elevation_value)

    _, mean_slope_250, _ = terrain_window_stats(elevation_src, x_5070, y_5070, 250)
    _, mean_slope_1000, ruggedness_1000 = terrain_window_stats(elevation_src, x_5070, y_5070, 1000)

    features["mean_slope_250m"] = float(mean_slope_250)
    features["mean_slope_1000m"] = float(mean_slope_1000)
    features["terrain_ruggedness_1000m"] = float(ruggedness_1000)

    distance_to_water = nearest_distance_m(hydro_combined_gdf, point)
    distance_to_road = nearest_distance_m(roads_gdf, point)

    features["distance_to_water_m"] = float(distance_to_water) if not np.isnan(distance_to_water) else float("nan")
    features["distance_to_road_m"] = float(distance_to_road) if not np.isnan(distance_to_road) else float("nan")

    for key in FEATURE_ORDER:
        if key not in features:
            raise RuntimeError(f"Missing required feature: {key}")

    for key, value in features.items():
        if isinstance(value, (float, np.floating)) and np.isnan(value):
            continue

        if key in {"forest_fraction_250m", "forest_fraction_1000m", "wetland_fraction_250m", "wetland_fraction_1000m", "developed_fraction_250m", "developed_fraction_1000m", "open_water_fraction_250m", "open_water_fraction_1000m"}:
            if value < 0 or value > 1:
                raise ValueError(f"{key} must be a fraction between 0 and 1, received {value}.")
        if key in {"distance_to_water_m", "distance_to_road_m"} and value < 0:
            raise ValueError(f"{key} must be non-negative, received {value}.")

    return {key: features[key] for key in FEATURE_ORDER}
