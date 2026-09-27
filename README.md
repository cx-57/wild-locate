# Wild-Locate

Wild-Locate estimates **relative habitat suitability** from wildlife observations and environmental data. It supports point and surrounding-area analysis, local accounts, model training, and both browser and desktop interfaces.

The suitability score is relative to a model's comparison locations. It is **not** the probability that a species is present.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Editable installation is the recommended development setup: changes in the repository are used directly without reinstalling the package.

## Run

Browser app:

```bash
wildlocate web
```

Desktop app:

```bash
wildlocate
```

Massachusetts environmental data:

```bash
wildlocate init
wildlocate status
```

Florida and Arizona use cached national raster tiles that download as needed.

## What it does

- Analyzes one location or an 81-point grid within a 10, 25, or 50 km radius.
- Reports relative suitability score, percentile, category, model, and environmental conditions.
- Includes bundled Massachusetts models for Bobcat, Coyote, Fisher, North American River Otter, and Red Fox.
- Trains custom **mammal and reptile** models from research-grade iNaturalist observations.
- Uses target-group background observations, environmental feature extraction, and spatial cross-validation.
- Stores custom models and enabled-model choices per local account.

## Conservation Deep Dive

A completed habitat assessment can launch a separate **Conservation Deep Dive** workspace. The ordinary assessment stays lightweight; Deep Dive deliberately performs a heavier second-stage analysis over the selected landscape.

For a regional assessment, Deep Dive researches the same 10, 25, or 50 km radius. For a single-point assessment, it expands to a 10 km research area around that point.

The current Deep Dive:

- resamples the landscape at 81 locations with the selected species model;
- compares the strongest and weakest modeled habitat;
- aggregates feature interpretations into regional habitat strengths and constraints;
- groups negative model effects into pressure domains such as development, road exposure, forest structure, water/wetlands, and terrain;
- identifies the broad landscape sector with the highest mean relative habitat percentile;
- runs the existing forest-cover / impervious-surface counterfactual scenarios and surfaces the largest modeled responses;
- provides an **Ask Wild-Locate** research panel whose answers are generated only from the completed Deep Dive report.

Deep Dive keeps model interpretation separate from ecological causation. Feature effects compare the current prediction with a prediction in which one feature is replaced by its comparison median. Pressure domains summarize negative model effects; they are not confirmed threats. Counterfactual scenarios are model experiments, not management recommendations.

### Current data scope

Deep Dive uses the environmental layers available to the species model—land cover, impervious surface, elevation/terrain, water context, and road context—and adds **USGS PAD-US 4.1** protection context for High and Very High sampled habitat locations.

For those stronger samples, Wild-Locate checks whether the sampled coordinate intersects a PAD-US managed/protected-area record. It separately counts records with **GAP Status 1 or 2**, which indicate management intent focused on maintaining biodiversity. These are point-overlap checks, not protected-acreage estimates, and a point with no PAD-US intersection should not be interpreted as definitively unprotected.

PAD-US is an external public service. If it is unavailable or only some lookups succeed, the habitat-model Deep Dive still completes and labels protection context unavailable or partial rather than fabricating results.

**Historical land-cover/development change is not connected yet.** That requires an additional time-series dataset before Wild-Locate can make claims about recent habitat loss or development trends.

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
│   ├── registry.py        # accounts + bundled/custom model registry
│   └── worker.py          # isolated prediction/training subprocesses
├── gui/
│   ├── app.py             # complete PyQt desktop interface
│   └── map.html           # desktop Leaflet map
└── web/
    ├── server.py          # loopback HTTP server
    ├── index.html
    ├── style.css
    └── app.js
```

The desktop and browser interfaces call the same Python prediction and training code. Model logic is not duplicated in JavaScript.

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
node --check wildlocate/web/app.js
node --test tests/web_client.test.cjs
```

For packaging regressions, verify the editable install from outside the repository:

```bash
cd /tmp
python -c "import wildlocate; print(wildlocate.__file__)"
wildlocate --help
```
