"""Experimental FL/AZ raster features, downloaded in reusable 120 km tiles.

Massachusetts never enters this path. New regions train their own raster-only
models; Massachusetts road/water distances are deliberately not substituted.
"""

from dataclasses import dataclass
from functools import lru_cache
import json
import math
import os
from pathlib import Path
from threading import Lock
import time
import uuid

import numpy as np
import rasterio
from rasterio.windows import Window
import requests
from shapely.geometry import Point, shape

from wildlocate.core.environment import (
    fraction, project_point, read_local_window, terrain_window_stats,
    validate_point_is_evaluable,
)
@dataclass(frozen=True)
class Region:
    code: str
    name: str
    bbox: tuple
    center: tuple
    place_id: int
    examples: tuple = ()


REGIONS = {
    'MA': Region('MA', 'Massachusetts', (-73.60, 41.10, -69.80, 42.95), (42.3718, -72.2820), 2),
    'FL': Region('FL', 'Florida', (-87.70, 24.35, -79.85, 31.10), (28.1, -81.6), 21,
                 ('Nine-banded Armadillo', 'Marsh Rabbit', 'Hispid Cotton Rat')),
    'AZ': Region('AZ', 'Arizona', (-114.90, 31.20, -108.95, 37.10), (32.25, -110.9), 40,
                 ('Collared Peccary', 'Black-tailed Jackrabbit', 'Desert Cottontail')),
}


def get_region(value='MA'):
    if isinstance(value, Region):
        value = value.code
    key = str(value).strip().upper()
    if key not in REGIONS:
        key = next((r.code for r in REGIONS.values() if r.name.casefold() == str(value).casefold()), key)
    if key not in REGIONS:
        raise ValueError('Choose Massachusetts, Florida or Arizona.')
    return REGIONS[key]


def region_root(region='MA'):
    from wildlocate.core.environment import get_user_data_dir
    code = get_region(region).code
    root = get_user_data_dir()
    return root if code == 'MA' else root / 'regions' / code




SCHEMA = 'regional-raster-v1'
TILE_SIZE = 120000
MARGIN = 1200
EXPECTED_CRS = rasterio.crs.CRS.from_epsg(5070)
ELEVATION_URL = 'https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage'
RETRYABLE_HTTP_STATUS = {403, 429, 500, 502, 503, 504}
# The National Map export service is reliable for individual 120 km requests but
# can return transient 5xx responses during bulk export work. NLCD downloads stay
# parallel; only 3DEP image exports are serialized and retried with backoff.
_ELEVATION_DOWNLOAD_LOCK = Lock()
CLASSES = {'forest': {41,42,43}, 'wetland': {90,95}, 'developed': {21,22,23,24},
           'open_water': {11}, 'shrubland': {52}, 'grassland': {71}, 'barren': {31},
           'cropland': {81,82}}
BOUNDARIES = Path(__file__).with_name('state_boundaries.geojson')


@lru_cache(maxsize=2)
def boundary(code):
    document = json.loads(BOUNDARIES.read_text())
    feature = next(f for f in document['features'] if f['properties']['STUSAB'] == code)
    return shape(feature['geometry'])


def validate_location(region, latitude, longitude):
    region = get_region(region)
    if region.code == 'MA':
        return
    if not math.isfinite(latitude) or not math.isfinite(longitude) or not boundary(region.code).covers(Point(longitude,latitude)):
        raise ValueError(f'Requested coordinate cannot be evaluated: choose a location in {region.name}.')


def initialize(region, progress=print):
    region = get_region(region)
    if region.code == 'MA':
        raise ValueError('Use the existing Massachusetts data setup.')
    boundary(region.code)
    root = region_root(region)
    root.mkdir(parents=True, exist_ok=True)
    progress(f'{region.name} uses cached national raster tiles. Tiles download as locations are analyzed or trained.')
    return root


def _valid_raster(path, x, y):
    """Return True only for a readable one-band EPSG:5070 raster covering x/y."""
    path = Path(path)
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        with rasterio.open(path) as src:
            bounds = src.bounds
            if src.crs != EXPECTED_CRS or src.count != 1 or src.width <= 0 or src.height <= 0:
                return False
            if not all(math.isfinite(value) for value in (bounds.left, bounds.bottom, bounds.right, bounds.top)):
                return False
            if not (bounds.left < bounds.right and bounds.bottom < bounds.top):
                return False
            if not (bounds.left <= x <= bounds.right and bounds.bottom <= y <= bounds.top):
                return False
            row, col = src.index(x, y)
            if row < 0 or row >= src.height or col < 0 or col >= src.width:
                return False
            # Opening metadata alone can succeed for some truncated TIFFs. Reading
            # the requested cell proves the data block itself is accessible.
            src.read(1, window=Window(col, row, 1, 1))
        return True
    except (OSError, ValueError, rasterio.errors.RasterioError):
        return False


def _download_elevation_tile(bbox, output_path, attempts=6):
    """Download a 3DEP tile, retrying transient failures without concurrent exports."""
    if attempts < 1:
        raise ValueError('attempts must be at least 1')

    params = {
        'bbox': ','.join(map(str, bbox)),
        'bboxSR': 5070,
        'imageSR': 5070,
        'size': '1360,1360',
        'format': 'tiff',
        'pixelType': 'F32',
        'noData': -9999,
        'interpolation': 'RSP_BilinearInterpolation',
        'f': 'image',
    }
    output_path = Path(output_path)

    for attempt in range(attempts):
        try:
            retry_delay = None
            with _ELEVATION_DOWNLOAD_LOCK:
                with requests.get(ELEVATION_URL, params=params, timeout=240, stream=True) as response:
                    if response.status_code in RETRYABLE_HTTP_STATUS and attempt < attempts - 1:
                        retry_delay = min(2 ** (attempt + 1), 30)
                    else:
                        response.raise_for_status()
                        with output_path.open('wb') as stream:
                            for chunk in response.iter_content(1024 * 1024):
                                if chunk:
                                    stream.write(chunk)
                        return
            if retry_delay is not None:
                time.sleep(retry_delay)
                continue
        except (requests.Timeout, requests.ConnectionError):
            if attempt >= attempts - 1:
                raise
            time.sleep(min(2 ** (attempt + 1), 30))


def tile_paths(region, x, y, progress=None):
    from wildlocate.core.environment import (
        download_tile, LANDCOVER_WCS, LANDCOVER_COVERAGE,
        IMPERVIOUS_WCS, IMPERVIOUS_COVERAGE,
    )
    col, row = math.floor(x / TILE_SIZE), math.floor(y / TILE_SIZE)
    folder = region_root(region) / 'raster-v1' / f'{col}_{row}'
    folder.mkdir(parents=True, exist_ok=True)
    bbox = (col*TILE_SIZE-MARGIN,row*TILE_SIZE-MARGIN,
            (col+1)*TILE_SIZE+MARGIN,(row+1)*TILE_SIZE+MARGIN)
    paths = {}
    for kind in ('landcover','impervious','elevation'):
        path = folder / f'{kind}.tif'
        paths[kind] = path
        if _valid_raster(path, x, y):
            continue
        # A stale zero-byte, truncated, wrong-CRS, or non-covering file must not
        # poison the cache forever. Remove it before creating a fresh temp file.
        path.unlink(missing_ok=True)
        if progress:
            progress(f'Downloading {get_region(region).name} {kind} tile {col}, {row}…')
        temporary = folder / f'.{kind}.{uuid.uuid4().hex}.tif'
        try:
            if kind != 'elevation':
                service, coverage = ((LANDCOVER_WCS,LANDCOVER_COVERAGE) if kind=='landcover'
                                     else (IMPERVIOUS_WCS,IMPERVIOUS_COVERAGE))
                download_tile(service,coverage,bbox,2025,temporary)
            else:
                _download_elevation_tile(bbox, temporary)
            if not _valid_raster(temporary, x, y):
                raise ValueError(f'Downloaded {kind} tile is not a valid EPSG:5070 raster covering the requested point.')
            os.replace(temporary,path)
        finally:
            temporary.unlink(missing_ok=True)
    return paths


def extract_regional_features(latitude, longitude, region, progress=None):
    validate_location(region,latitude,longitude)
    x,y=project_point(latitude,longitude)
    paths=tile_paths(region,x,y,progress)
    features={}
    with rasterio.open(paths['landcover']) as lc, rasterio.open(paths['impervious']) as imp, rasterio.open(paths['elevation']) as dem:
        for src in (lc,imp,dem):
            validate_point_is_evaluable(x,y,src,'regional raster')
        for radius in (250,1000):
            data,mask,*_=read_local_window(lc,x,y,radius)
            values=data[mask & (data>0)]
            for name,classes in CLASSES.items():
                features[f'{name}_fraction_{radius}m']=fraction(values,classes)
            data,mask,*_=read_local_window(imp,x,y,radius)
            values=data[mask & (data>=0) & (data<=100)]
            features[f'mean_impervious_{radius}m']=float(np.mean(values)) if len(values) else float('nan')
            _,slope,ruggedness=terrain_window_stats(dem,x,y,radius)
            features[f'mean_slope_{radius}m']=slope
            if radius==1000:
                features['terrain_ruggedness_1000m']=ruggedness
        row,col=dem.index(x,y)
        features['elevation_m']=float(dem.read(1,window=Window(col,row,1,1))[0,0])
        if features['elevation_m']==dem.nodata or not all(np.isfinite(v) for v in features.values()):
            raise ValueError('Requested coordinate cannot be evaluated: regional raster data is incomplete.')
    return features


def prefetch_training_tiles(points, region, progress=None):
    """Fetch distinct tiles with bounded concurrency before feature extraction."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    tiles = {}
    for row in points.itertuples():
        try:
            validate_location(region, float(row.latitude), float(row.longitude))
        except ValueError:
            continue  # The feature builder records these as failures explicitly.
        x, y = project_point(float(row.latitude), float(row.longitude))
        tiles.setdefault((math.floor(x/TILE_SIZE), math.floor(y/TILE_SIZE)), (x,y))
    with ThreadPoolExecutor(max_workers=3) as executor:
        jobs = [executor.submit(tile_paths, region, x, y) for x,y in tiles.values()]
        for index, future in enumerate(as_completed(jobs), 1):
            future.result()
            if progress:
                progress(f"Prepared {index} of {len(jobs)} environmental tiles for {get_region(region).name}…")
