# Understanding Wild-Locate’s GUI

The GUI is the part you see and click. It collects selections, draws results, and
asks the backend to do analysis or training. Habitat calculations live in
`wildlocate/core/`, not in these window classes.

## The folder after consolidation

```text
wildlocate/gui/
├── __init__.py          Python package marker (empty)
├── app.py               Main window and application startup
├── components.py        Styling, formatting, reusable widgets, insights
├── dialogs.py           Login and Manage species windows
├── workers.py           Communication with background Python processes
├── location_map.py      Embedded browser and Python ↔ JavaScript bridge
└── map/
    ├── index.html       Map page structure
    ├── map.css          Map styling
    ├── map.js           Map behavior and regional dots
    └── vendor/          Bundled Leaflet library, stylesheet, and license
```

Ten working Python modules became five. `__init__.py` remains the package marker.
The amount of application logic is essentially the same; related code now lives
together. The browser files stay separate because HTML, CSS, and JavaScript have
different jobs from Python. Vendored Leaflet is third-party code, not our GUI logic.

| Former file | New home |
| --- | --- |
| `app.py` | `app.py` |
| `theme.py`, `formatting.py`, `widgets.py`, `insights.py` | `components.py` |
| `login.py`, `species_manager.py` | `dialogs.py` |
| `client.py`, `training_client.py` | `workers.py` |
| `location_map.py` | `location_map.py` |

Internal imports use the new names. For example, import `PredictionClient` from
`wildlocate.gui.workers` and `STYLESHEET` from `wildlocate.gui.components`.
The removed module names are no longer import paths.

## A few Python and Qt ideas

- A **class** describes an object. `MainWindow` describes the main window.
- `__init__` runs when an object is created. It builds widgets and connects events.
- `self` means this particular object. `self.species` is its species dropdown.
- A **widget** is a screen element: a button, label, dropdown, table, or window.
- A **layout** positions widgets. `QVBoxLayout` stacks them vertically;
  `QHBoxLayout` puts them side by side; `QGridLayout` uses rows and columns.
- A **signal** announces an event. Connecting a signal to a function tells Qt what
  to run when that event happens. `button.clicked.connect(self.analyze)` means
  “when clicked, call this window’s analyze method.”
- A **slot** is a function Qt can call in response to a signal. `@pyqtSlot` also
  exposes selected Python methods to the map’s JavaScript bridge.
- `app.exec()` runs the event loop: Qt waits for input, paints windows, and handles
  process messages until the application exits.
- `setObjectName("primary")` gives a widget a styling name. The stylesheet’s
  `QPushButton#primary` rule determines that button’s appearance.

## How launching works

```text
run.command
  → .venv/bin/python -m wildlocate
  → wildlocate/__main__.py
  → wildlocate/cli.py: main() → cmd_gui()
  → gui/app.py: main()
  → create_application()
  → LoginDialog
  → MainWindow
  → Qt event loop
```

The macOS launcher selects the project’s Python environment and repairs hidden
file flags that can prevent Qt from finding plugins. The CLI launches the GUI
when no other command is given. `create_application()` sets the application name,
icon-related settings, palette, fonts, and stylesheet. `main()` presents login,
opens the main window for that account, and returns to login after sign-out.

## app.py — the main application

This is the best starting point for changing the screen layout or user flow.
`button()` is a small helper that creates a styled button and connects its action.
`MainWindow` owns the current state, inputs, map, worker client, and result views.

Important state:

- `region`: selected state (`MA`, `FL`, or `AZ`), distinct from analysis radius.
- `username`: account used to find that person’s enabled models.
- `result`: latest assessment dictionary, or `None` when nothing is current.
- `client`: `PredictionClient`, which manages the analysis subprocess.
- `timer` and `_started_at`: update the elapsed-time display.
- `stack`: switches between the empty/loading view, point results, and area results.

### Building and arranging the window

| Method | Purpose |
| --- | --- |
| `__init__` | Builds the window, creates widgets, connects events and shortcuts. |
| `build_nav` | Brand, account sign-out, Manage species, and state selector. |
| `build_hero` | Title and introductory text. |
| `build_inputs` | Species, coordinates, analysis type, radius, analyze/cancel buttons. |
| `build_results` | Large map, loading view, point assessment, regional summary and collapsible table. |
| `build_methodology` | Expandable explanation of the assessment and its limits. |
| `fit_result_height` | Fits the results panel to the active view, avoiding excess empty space. |
| `responsive_layout` | Switches the cards between side-by-side and stacked arrangements. |
| `resizeEvent` | Reapplies the responsive arrangement when the window changes size. |

The map is created while building inputs and placed in the results card. Both
sides therefore use the same map object, rather than two independent maps.

### Keeping selections consistent

| Method | Purpose |
| --- | --- |
| `change_region` | Switches state, refreshes species, and selects an example location. |
| `use_example` | Fills suitable example coordinates for the selection. |
| `manage_species` | Opens the model-management dialog for this account and state. |
| `refresh_species` | Rebuilds the available species list after models change. |
| `map_selected` | Copies a map click into the coordinate fields. |
| `sync_map` | Sends valid typed coordinates back to the map. |
| `analysis_changed` | Shows/hides the radius selector and updates the circle. |
| `inputs_changed` | Clears old results when they no longer match the selection. |
| `read_coordinates` | Converts input text into numbers and displays validation errors. |

`QSignalBlocker` temporarily suppresses signals when code changes a field. This
prevents a map click updating a field from triggering redundant update cycles.

### Running and displaying an assessment

1. `analyze()` validates the species and coordinates, clears stale results, enters
   the busy state, and sends the selected radius (or `None` for point analysis).
2. `set_busy()` disables selection controls while work runs and enables Cancel.
   `update_elapsed()` refreshes the elapsed time.
3. `show_result()` receives a dictionary from the worker. It fills the point
   assessment, or delegates regional results to `show_area_result()`.
4. `show_area_result()` updates the summary, table, legend-related result state,
   and map dots. `show_insights()` builds the point-only explanatory panel.
5. `show_error()` displays a failed assessment. `cancel_if_busy()` responds to
   Escape; `cancelled()` restores the screen after cancellation.
6. `export_result()` asks for a JSON destination and uses `QSaveFile` to publish
   the complete file atomically. It includes the result and interpretation.
7. `sign_out()` marks the session as finished and closes the window.
   `closeEvent()` stops the worker and shuts down the browser resources.

## components.py — appearance and reusable pieces

The file has four labeled sections so it remains easy to navigate.

### Palette and stylesheet

`light_palette()` supplies colors for native Qt controls. `STYLESHEET` contains
Qt’s CSS-like rules for labels, cards, buttons, dropdowns, tables, and scrollbars.
Change these rules for fonts, colors, spacing, borders, and selected states.
They style desktop widgets; the map’s web content uses `map/map.css` instead.

### Formatting

- `ordinal(81)` returns `81st`, including the special rules for 11th–13th.
- `coordinates()` turns signed latitude/longitude into degrees and N/S/E/W text.
- `feature_display()` translates backend feature names into readable labels and
  units: fractions become percentages, slope gets degrees, distances get meters,
  and non-finite values display as unavailable.

### Reusable widgets

- `label()` creates consistently styled plain-text labels; `divider()` creates a line.
- `ChoiceBox` customizes a dropdown’s popup width, styling, and painted chevron.
- `draw_mark()` draws the leaf symbol. `app_icon()` returns it as a window icon;
  `BrandMark` paints it directly into the screen.
- `SuitabilityGauge` draws the five category bands and the selected percentile.
  `set_percentile()` updates its value; `paintEvent()` performs the drawing.
- `Disclosure` is an expandable section. It owns a toggle button and a body layout.
  `set_expanded()` updates the button and shows or hides the body.

### Insights

`InsightsPanel` displays backend-provided habitat explanations in two tabs:
local conditions affecting the score and the model’s strongest features.
`text()` makes a wrapped text label; `row()` formats a condition and score effect.
The panel selects and formats explanations; it does not calculate them or retrain
the model. It also handles the backend reporting that insights are unavailable.

## dialogs.py — additional windows

`LoginDialog` builds the local sign-in/create-account form. `toggle_mode()` switches
between those modes, clears password fields, and shows the confirmation field.
`sign_in()` checks matching passwords when creating an account, delegates to
`core.accounts.authenticate()`, displays errors, or accepts the dialog on success.
Authentication and password storage are backend responsibilities.

`SpeciesManager` handles saved models and training. `action()` creates its buttons.

| Methods | Purpose |
| --- | --- |
| `__init__`, `build_models`, `build_training` | Build the model list, review area, and training workflow. |
| `selected_record`, `refresh_models`, `show_model` | Find the selection, reload available models, and show validation metrics. |
| `update_controls` | Enables only actions allowed by the current training state. |
| `query_changed` | Invalidates a previously selected species when search text changes. |
| `send` | Sends a named operation through `TrainingClient`. |
| `find_species` | Begins species lookup after clearing the previous attempt. |
| `prepare_data` | Requests observation and environmental-data preparation. |
| `download_environment` | Requests missing environmental datasets. |
| `start_training` | Starts training after preparation succeeds. |
| `handle_event` | Processes progress, resolved, prepared, initialized, completed, cancelled, and error events. |
| `append_log` | Adds worker diagnostics to the expandable training log. |
| `enable_selected` | Makes a reviewed model available for analysis and announces the change. |
| `retrain_selected` | Starts the training flow for a saved species. |
| `delete_selected` | Confirms and deletes a custom model; bundled models are protected. |
| `reject`, `closeEvent` | Confirm cancellation if needed and stop the worker before closing. |

The dialog keeps the selected taxon, preparation status, workflow state, and model
records. A completed model is reviewed before the user enables it.

## workers.py — background communication

These are Qt-side clients, not the actual prediction/training algorithms.
Each uses `QProcess` to run another Python process so slow work does not freeze
window interaction. Messages are JSON followed by a newline.

`PredictionClient` starts `core.prediction_worker`. `analyze()` stores a request
and starts or reuses the subprocess. `send_pending()` writes the request.
`read_output()` buffers incoming bytes until a full line is available, decodes
JSON, and emits `succeeded` or `failed`. `process_error()` and `finished()` report
startup failures and unexpected exits. `read_error()` drains diagnostic output.
`close()` stops the process; `cancel()` also emits the cancellation signal.

`TrainingClient` starts `core.training_worker` with the account, state, and a job
identifier. `request()` submits actions such as resolve, prepare, or train.
Its `read_output()` handles multiple progress messages before a final event.
`read_log()` forwards diagnostics to the log panel. `finished()` cleans up temporary
job data and reports unexpected termination. `close()` and `cancel()` stop work;
`_stopping` prevents intentional termination being reported as a crash.

Both clients use the console Python executable when launched from Windows’
`pythonw`, because the subprocess needs working input/output pipes.

## location_map.py — connecting Python to the browser

`LocationMap` embeds a `QWebEngineView` and loads the local map HTML.
If WebEngine is unavailable, it shows a fallback message and leaves manual
coordinate input usable.

`MapBridge` exposes three JavaScript-callable methods: `mapReady`, `selectLocation`,
and `tilesAvailable`. These become ordinary Python signals. Coordinate values
are checked before being emitted.

`MapPage.acceptNavigationRequest()` keeps the embedded browser on its local map
page and opens explicitly clicked HTTPS links in the system browser.

`LocationMap` methods:

- `map_ready()` marks the browser ready and sends pending state.
- `loaded()`, `render_failed()`, and `tile_status()` update map availability messages.
- `select_location()` forwards accepted clicks when interaction is enabled.
- `set_location()` stores the center; `set_area()` stores radius and grid results.
- `setEnabled()` also updates browser interaction state.
- `send_state()` serializes the current state and calls JavaScript’s
  `window.setLocationState(...)`. It preserves recenter requests made before load.
- `shutdown()` stops the view and releases the page, view, and profile in order.

## map/ — what the embedded browser runs

`index.html` defines the map container and Show state button. It loads the styles,
Leaflet, Qt’s web-channel script, and our map script. Its content-security policy
limits the resources the page can load.

`map.css` controls the web map’s colors, pin, controls, focus outline, and busy cursor.

`map.js` owns the Leaflet map:

- Creates the map and OpenStreetMap tile layer; reports tile availability to Python.
- `selectLocation()` rounds/wraps a map click and sends it through the Qt bridge.
- `setLocationState()` removes stale overlays, draws the selected pin or regional
  circle and sample dots, and moves the view when the center/radius changes.
- Each result marker uses its percentile category for color and a popup for its
  score. Missing-data points are gray. Clicking a result dot does not pick a new center.
- The Show state button restores the state overview; `ResizeObserver` keeps the
  map correctly sized when the desktop layout changes.

`vendor/leaflet.js` and `vendor/leaflet.css` supply the mapping library.
`vendor/LICENSE` contains its license. Usually edit our `map.js` or `map.css`, not
these bundled third-party files.

## One complete analysis request

```text
Click Analyze
  → MainWindow.analyze()
  → PredictionClient.analyze()
  → JSON request to core.prediction_worker
  → core.service.assess_habitat()
  → core.predict.predict_species() OR core.area.predict_area()
  → JSON result back to PredictionClient
  → succeeded signal
  → MainWindow.show_result()
  → labels/table + LocationMap.set_area()
  → JavaScript setLocationState()
  → updated map
```

## Where to edit common things

| Desired change | Start here |
| --- | --- |
| Buttons, controls, page arrangement | `app.py` build methods |
| Regional result summary/table | `app.py: show_area_result()` and `build_results()` |
| Desktop fonts, colors, borders | `components.py: STYLESHEET` |
| Coordinate/unit formatting | `components.py` formatting functions |
| Reusable dropdowns or collapsible sections | `components.py` widget classes |
| Login or model-management screens | `dialogs.py` |
| Worker messages or cancellation | `workers.py` and corresponding `core/*_worker.py` |
| Map size/browser behavior | `location_map.py` |
| Dot size, map popups, radius outline | `map/map.js` |
| Map control styling | `map/map.css` |
| Scoring/model logic | `core/predict.py`, `core/area.py`, and related backend modules |

The launcher remains `./run.command`. This consolidation changes file organization,
not the intended application behavior.
