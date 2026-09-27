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

## Conservation screening

Regional analysis includes **Conservation Screening** in both the browser and desktop interfaces. It asks where habitat already scores strongly and where the model indicates the greatest restoration potential among the sampled locations.

- **Habitat distribution:** counts and percentages by suitability category, excluding unavailable points. Percentages describe evaluated samples, not land area.
- **Existing high-suitability locations:** up to five High or Very High locations (current percentile at least 60), ranked by current percentile. If no samples qualify, the list is empty.
- **Modeled restoration opportunities:** up to five locations ranked by positive percentile improvement. Ties retain sampled-point order. No weighted conservation score is used.

Select a candidate card or map point to view its current suitability, major environmental drivers, and best model-based restoration scenario, including changed variables and current → projected percentile. Selecting a candidate highlights the existing point and zooms the map without changing the analysis center.

The scenario search reuses the measured feature frame. It tests replacing 10%, 25%, or 50% of developed cover with forest, or reducing impervious surface by those proportions, at the supported 250 m and 1000 m scales. It preserves the existing point-analysis search: retain the largest score gain per scenario type, with a minimum score gain of 0.001. The regional best scenario is chosen by projected percentile, then score gain. A score gain without a percentile gain can appear in point details but does not qualify for the restoration ranking. Missing scenario features or no positive tested changes produce no scenario; calculation failures are reported separately while preserving valid suitability scores.

Drivers compare the current score with a prediction where one feature is replaced by its comparison median. These separate comparisons do not add up to the score and are not causal effects.

**Interpretation limits:**

- Suitability is not presence probability.
- The **81 points are sampled locations, not continuous habitat coverage**. Missing environmental coverage can further reduce the evaluated sample.
- Restoration scenarios are **counterfactual ML outputs, not causal predictions**. They do not establish intervention feasibility, ecological outcomes, ownership, cost, or conservation priority. Changed feature combinations may be outside the conditions represented in training data.
- Each listed location is a **candidate for further investigation**, not a recommendation. An empty restoration list does not establish that restoration is impossible.
- This screening does not identify habitat patches or corridors.

JSON exports retain per-point `features`, `insights`, and `restoration`, plus the regional `conservation` summary and interpretation limitations. Each summary candidate has a zero-based `point_index` referencing the original `points` array, including unavailable entries. A null restoration means no positive scenario was found or the calculation failed; consult `insights.error` to distinguish failures. Exported percentiles are relative to the species model's comparison locations; percentile delta is in percentile points.

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
