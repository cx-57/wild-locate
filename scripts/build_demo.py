"""Regenerate the bundled example using real installed environmental datasets.

Run from the repository: python scripts/build_demo.py
No synthetic scores or observations are used.
"""
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path

from wildlocate.core.predict import predict_area, predict_species


def main():
    species, latitude, longitude = "Fisher", 42.3732, -72.5199
    point = predict_species(species, latitude, longitude)
    area = predict_area(species, latitude, longitude, 10)
    payload = {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "description": "Recorded model assessment around Amherst, Massachusetts. Hypothetical changes are model experiments, not ecological forecasts.",
        "runtime": {name: importlib.metadata.version(name)
                    for name in ("scikit-learn", "xgboost", "rasterio", "geopandas")},
        "point": point, "area": area,
    }
    path = Path(__file__).resolve().parents[1] / "wildlocate" / "web" / "demo.json"
    path.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Recorded real example: {path}")
    print(f"Point percentile: {point['percentile']}; area samples: {area['evaluated_points']}")


if __name__ == "__main__":
    main()
