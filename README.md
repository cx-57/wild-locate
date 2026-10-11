# Wild-Locate

Wild-Locate estimates **relative habitat suitability** from wildlife observations and environmental data. It supports point and surrounding-area analysis, local species-model training, a browser-based **Deep Dive** into specific mapped places, and a desktop habitat explorer.

The suitability score is relative to a model's comparison locations. It is **not** the probability that a species is present.

## Install

Requires **Python 3.10+**. An internet connection is needed for initial environmental-data downloads, species training, and uncached online mapping.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Editable installation is the recommended development setup: changes in the repository are used directly without reinstalling the package.

## Run

Prepare Massachusetts environmental data before analyzing a location there (one-time setup):

```bash
wildlocate init
wildlocate status
```

Launch the **browser app** (recommended for Deep Dive):

```bash
wildlocate web
```

It opens a local browser page at `http://127.0.0.1:8765`. Keep the terminal running. If port 8765 is occupied, use `wildlocate web --port 8766` and open the URL printed in the terminal. If the `wildlocate` command is not found, use `./.venv/bin/wildlocate` from the project directory or activate `.venv` again.

Launch the **desktop app** for point/regional habitat assessments:

```bash
wildlocate
```

The browser app allows exploration without signing in; training, enabling, and managing custom models require a local account. Florida and Arizona use separate, experimental raster-based models: **train a model for the chosen state first**. Their uncached environmental raster tiles download on demand.

## What it does

- Analyzes one location or an 81-point grid within a 10, 25, or 50 km radius.
- Reports relative suitability score, percentile, category, model, and environmental conditions.
- Includes bundled Massachusetts models for Bobcat, Coyote, Fisher, North American River Otter, and Red Fox.
- Trains custom **mammal and reptile** models from research-grade iNaturalist observations.
- Uses target-group background observations, environmental feature extraction, and spatial cross-validation.
- Supports Massachusetts, Florida, and Arizona with region-specific environmental inputs and models.
- Stores custom models and enabled-model choices per local account.

## Deep Dive (browser app)

Deep Dive opens as a separate **page within the app**, not a new browser tab. The initial assessment is deliberately compact: it shows relative suitability (or a short sampled-area summary), the **Open Deep Dive** action, and an optional collapsed section for model explanations, hypothetical scenarios, detailed scores, and validation evidence.

A completed habitat assessment can open a separate Deep Dive for the same landscape. **A point assessment starts Deep Dive across the surrounding 10 km radius**, rather than analyzing only the originally selected coordinate. A regional assessment retains its chosen 10, 25, or 50 km radius. Both transitions disclose their study scope in the interface.

Deep Dive is a **specific-place investigation**, not a duplicate habitat summary.
Its main view is a map of individual roads, building footprints, habitat polygons,
and terrain sampling coordinates. Clicking a place shows the corresponding
geometry, species-model assessment, limiting or supporting environmental
conditions, possible mechanisms to investigate, source links, and field checks.

The supporting spatial sample grid remains behind the scenes to locate useful
search neighborhoods. Coverage and training/validation evidence are disclosed,
but regional direction rankings, generic distribution charts, and landscape
pressure lists are **not** part of the Deep Dive interface.

In the browser app, both habitat assessments and Deep Dive reports can be exported as JSON or printed/saved as PDF. The desktop app exports habitat assessments as JSON. Both browser maps support scroll-wheel and trackpad pinch zoom. Point examples are recorded results; opening Deep Dive runs a fresh analysis with local data.

These are model interpretations, not proof of ecological causation.

Specific places use the existing local machine-learning model, without a paid
AI service or API key. Deep Dive searches the selected center and up to five additional 1 km
neighborhoods, retrieves bounded OpenStreetMap geometry through Overpass,
and evaluates up to six mapped candidates of each kind plus two real raster-scored
terrain coordinates. It displays up to three roads, three buildings, and three
habitat/terrain sites (with a cap of two terrain samples). Sites with neutral
or unavailable model comparisons remain visible rather than disappearing.
Comparisons are ranked by their strongest relevant negative individual
condition, without adding overlapping effects into a damage score.

The model measures surrounding conditions, usually within 250 m or 1 km;
it cannot establish that an individual structure caused ecological harm or
predict what would happen if that structure were removed. Mapping and raster
data may have different dates, and mapping coverage is incomplete. Failed
online lookups preserve the report and can use cached Massachusetts road
geometry and existing terrain samples, with missing building data clearly
identified. A single map-response cache is capped at 3 MB and expires after
24 hours; network time and response size are bounded.

## Architecture

```text
wildlocate/
├── cli.py
├── core/
│   ├── environment.py     # datasets, downloads, MA environmental features
│   ├── observations.py    # iNaturalist presence + target-group background data
│   ├── regional.py        # region definitions + FL/AZ raster features
│   ├── modeling.py        # dataset construction, CV, training sessions
│   ├── predict.py         # point/area prediction + request validation
│   ├── deep_dive.py       # regional samples + Deep Dive report
│   ├── registry.py        # accounts + bundled/custom model registry
│   ├── mapped_places.py   # bounded OSM geometry + local MassDOT fallback
│   ├── place_analysis.py  # actual-place model comparisons and field priorities
│   └── worker.py          # isolated prediction/training subprocesses
├── gui/
│   ├── app.py             # desktop map and main window
│   ├── views.py           # desktop result panels
│   ├── dialogs.py         # account and species dialogs
│   ├── jobs.py            # background Qt processes
│   ├── ui.py              # shared widgets and styles
│   └── map.html           # desktop Leaflet map
└── web/
    ├── server.py          # local HTTP routes
    ├── jobs.py            # background server processes
    ├── index.html
    ├── style.css
    ├── app.js             # shared state and map
    ├── habitat.js         # habitat results
    ├── deep-dive.js       # individual-place analysis
    ├── report.js          # printing and navigation
    └── species.js         # species training
```

The desktop and browser interfaces share the same Python prediction and training code. Deep Dive's separate, specific-place report is available in the browser app; its model logic also lives in Python, not JavaScript.

## Modeling

Training resolves a species through iNaturalist, downloads research-grade non-captive observations, removes obscured/duplicate/imprecise locations, and requires at least 25 usable observations.

Background points come from the same broad target group as the trained species:

- Mammals → Mammalia background pool
- Reptiles → Reptilia background pool

Candidate Logistic Regression, Random Forest, and XGBoost models are evaluated with spatial cross-validation. Selection prioritizes mean PR-AUC, then ROC-AUC, then lower model complexity. The selected model is refit on the full training dataset and saved for review before it is enabled.

## Environmental features

Massachusetts models use NLCD land cover and impervious surface, USGS 3DEP elevation/terrain, MassDEP hydrography, and MassDOT roads.

Florida and Arizona use the `regional-raster-v1` schema with cached NLCD and 3DEP tiles. Regional models are separate from Massachusetts models.

## Storage

Bundled models ship with the package. Downloaded environmental data, accounts, training workspaces, and custom models live in the platform-specific Wild-Locate application-data directory.

Set `WILDLOCATE_DATA_DIR` to override that location.

## Development checks

```bash
python -m compileall -q wildlocate tests
python -m unittest discover -s tests -p "test_*.py"
for file in wildlocate/web/*.js; do node --check "$file"; done
node --test tests/web_client.test.cjs
```

For packaging regressions, verify the editable install from outside the repository:

```bash
cd /tmp
python -c "import wildlocate; print(wildlocate.__file__)"
wildlocate --help
```
