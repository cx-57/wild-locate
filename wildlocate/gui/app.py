"""Wild-Locate desktop interface"""

import json
import math
import sys
import time
from pathlib import Path
from PyQt6.QtCore import QIODevice, QObject, QSaveFile, QSignalBlocker, Qt, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDesktopServices, QFont, QFontDatabase, QKeySequence, QShortcut
from PyQt6.QtWidgets import QAbstractItemView, QApplication, QBoxLayout, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLineEdit, QMainWindow, QProgressBar, QScrollArea, QSizePolicy, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
from wildlocate.core.registry import available_species, normalize_username
from wildlocate.core.regional import REGIONS, get_region
from wildlocate.gui.ui import STYLESHEET, light_palette, ordinal, coordinates, feature_display, label, divider, ChoiceBox, app_icon, BrandMark, SuitabilityGauge, Disclosure, InsightsPanel
from wildlocate.gui.jobs import PredictionClient
from wildlocate.gui.dialogs import LoginDialog, SpeciesManager, button

try:
    from PyQt6.QtWebChannel import QWebChannel
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
    from PyQt6.QtWebEngineWidgets import QWebEngineView
except ImportError:
    QWebEngineView = None

MAP_FILE = Path(__file__).with_name("map.html")


class MapBridge(QObject):
    ready = pyqtSignal()
    selected = pyqtSignal(float, float)
    tile_status = pyqtSignal(bool)

    @pyqtSlot()
    def mapReady(self):
        self.ready.emit()

    @pyqtSlot(float, float)
    def selectLocation(self, latitude, longitude):
        if math.isfinite(latitude) and math.isfinite(longitude) and -90 <= latitude <= 90 and -180 <= longitude <= 180:
            self.selected.emit(latitude, longitude)

    @pyqtSlot(bool)
    def tilesAvailable(self, available):
        self.tile_status.emit(available)


if QWebEngineView is not None:
    class MapPage(QWebEnginePage):
        def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
            if url == QUrl.fromLocalFile(str(MAP_FILE)):
                return True
            if navigation_type == self.NavigationType.NavigationTypeLinkClicked and url.scheme() == "https":
                QDesktopServices.openUrl(url)
            return False


class LocationMap(QWidget):
    location_selected = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ready = False
        self._pending_recenter = False
        self._location = (42.3718, -72.2820)
        self._radius_km = None
        self._area_points = []
        self.region_center = (42.2, -71.7)
        self.view = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        self.status = label("Loading map… Manual coordinates are also available below.", "small", True)
        if QWebEngineView is None:
            self.status.setText("Map unavailable. Install PyQt6-WebEngine to enable it, or enter coordinates below.")
            layout.addWidget(self.status)
            return

        self.profile = QWebEngineProfile("WildLocateMap", self)
        self.profile.setHttpUserAgent("Wild-Locate/1.0 (desktop habitat explorer)")
        self.profile.setHttpCacheMaximumSize(64 * 1024 * 1024)
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        self.view = QWebEngineView(self)
        self.view.setMinimumHeight(300)
        self.view.setAccessibleName("Location map. Click to choose a location, or enter coordinates below.")
        self.page = MapPage(self.profile, self.view)
        self.view.setPage(self.page)
        self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        self.channel = QWebChannel(self.page)
        self.bridge = MapBridge(self.channel)
        self.channel.registerObject("locationBridge", self.bridge)
        self.page.setWebChannel(self.channel)
        self.bridge.ready.connect(self.map_ready)
        self.bridge.selected.connect(self.select_location)
        self.bridge.tile_status.connect(self.tile_status)
        self.view.loadFinished.connect(self.loaded)
        self.view.renderProcessTerminated.connect(self.render_failed)
        layout.addWidget(self.view)
        layout.addWidget(self.status)
        self.view.setUrl(QUrl.fromLocalFile(str(MAP_FILE)))

    def map_ready(self):
        self._ready = True
        self.send_state()

    def loaded(self, ok):
        if not ok:
            self._ready = False
            self.status.setText("Map could not load. Enter coordinates below to continue.")

    def render_failed(self, *_):
        self.loaded(False)

    def tile_status(self, available):
        self.status.setText(
            "Click to place a pin. Drag to explore; use + / − to zoom."
            if available else
            "Map tiles are unavailable. Check your connection, or enter coordinates below."
        )

    def select_location(self, latitude, longitude):
        if self.isEnabled():
            self.location_selected.emit(latitude, longitude)

    def set_location(self, latitude=None, longitude=None, *, recenter=False):
        self._location = (latitude, longitude)
        self.send_state(recenter=recenter)

    def setEnabled(self, enabled):
        super().setEnabled(enabled)
        self.send_state()

    def set_area(self, radius_km=None, points=None):
        self._radius_km = radius_km
        self._area_points = points or []
        self.send_state()

    def send_state(self, *, recenter=False):
        self._pending_recenter = self._pending_recenter or recenter
        if self._ready:
            recenter = self._pending_recenter
            self._pending_recenter = False
            state = {"latitude": self._location[0], "longitude": self._location[1], "enabled": self.isEnabled(), "recenter": recenter, "regionCenter": self.region_center}
            state.update(radiusKm=self._radius_km, areaPoints=self._area_points)
            self.page.runJavaScript(f"window.setLocationState({json.dumps(state, allow_nan=False)});")

    def shutdown(self):
        if self.view is not None:
            from PyQt6 import sip
            self.view.stop()
            sip.delete(self.page)
            sip.delete(self.view)
            sip.delete(self.profile)
            self.view = None
            self._ready = False

# Habitat prediction

class MainWindow(QMainWindow):
    def __init__(self, username=None):
        super().__init__()
        self.region = "MA"
        self.username = normalize_username(username) if username is not None else None
        self.signed_out = False
        self.setWindowTitle("Wild-Locate · Habitat Explorer")
        self.setWindowIcon(app_icon())
        self.resize(1240, 930)
        self.setMinimumSize(760, 640)
        self.result = None
        self.client = PredictionClient(self, username=self.username)
        self.client.succeeded.connect(self.show_result)
        self.client.failed.connect(self.show_error)
        self.client.cancelled.connect(self.cancelled)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.update_elapsed)
        self._started_at = 0

        shell = QWidget()
        shell.setObjectName("canvas")
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(self.build_nav())
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        canvas = QWidget()
        canvas.setObjectName("canvas")
        outer = QHBoxLayout(canvas)
        outer.setContentsMargins(36, 0, 36, 0)
        self.page = QWidget()
        self.page.setObjectName("page")
        self.page.setMaximumWidth(1220)
        self.page_layout = QVBoxLayout(self.page)
        self.page_layout.setContentsMargins(0, 0, 0, 24)
        self.page_layout.setSpacing(20)
        self.page_layout.addWidget(self.build_hero())

        self.cards = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.cards.setSpacing(22)
        self.input_card = self.build_inputs()
        self.result_card = self.build_results()
        self.cards.addWidget(self.input_card, 0)
        self.cards.addWidget(self.result_card, 1)
        self.page_layout.addLayout(self.cards)

        self.insights = Disclosure("Habitat Insights")
        self.page_layout.addWidget(self.insights)
        self.insights.hide()

        self.environment = Disclosure("Environmental Conditions")
        self.environment.body_layout.addWidget(label("Measured conditions at the selected location. These values do not indicate each feature's contribution to the score.", "muted", True))
        self.feature_table = QGridLayout()
        self.feature_table.setHorizontalSpacing(24)
        self.feature_table.setVerticalSpacing(12)
        self.environment.body_layout.addLayout(self.feature_table)
        self.page_layout.addWidget(self.environment)
        self.environment.hide()
        self.methodology = self.build_methodology()
        self.page_layout.addWidget(self.methodology)

        footer = QHBoxLayout()
        footer.addWidget(label("WildLocate", "small"))
        footer.addStretch()
        footer.addWidget(label("Zain Aboobacker & Charles Xie", "small"))
        self.page_layout.addLayout(footer)
        self.page_layout.addStretch()
        outer.addWidget(self.page)
        self.scroll.setWidget(canvas)
        shell_layout.addWidget(self.scroll)
        self.setCentralWidget(shell)
        self.responsive_layout()

        self.region_choice.currentIndexChanged.connect(self.change_region)
        self.species.currentTextChanged.connect(self.inputs_changed)
        self.analysis_type.currentIndexChanged.connect(self.analysis_changed)
        self.radius_choice.currentIndexChanged.connect(self.analysis_changed)
        for field in (self.latitude, self.longitude):
            field.textChanged.connect(self.inputs_changed)
            field.textChanged.connect(self.sync_map)
            field.editingFinished.connect(lambda: self.sync_map(recenter=True))
            field.returnPressed.connect(self.analyze)
        self.location_map.location_selected.connect(self.map_selected)
        self.sync_map()
        self.shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        self.shortcut.activated.connect(self.analyze)
        self.cancel_shortcut = QShortcut(QKeySequence("Escape"), self)
        self.cancel_shortcut.activated.connect(self.cancel_if_busy)
        self.setTabOrder(self.species, self.manual_coordinates.toggle)
        self.setTabOrder(self.manual_coordinates.toggle, self.latitude)
        self.setTabOrder(self.latitude, self.longitude)
        self.setTabOrder(self.longitude, self.analyze_button)
        self.species.setFocus(Qt.FocusReason.OtherFocusReason)

    def build_nav(self):
        nav = QWidget()
        nav.setObjectName("nav")
        row = QHBoxLayout(nav)
        row.setContentsMargins(38, 0, 38, 0)
        row.setSpacing(13)
        row.addWidget(BrandMark())
        row.addWidget(label("Wild-Locate", "brand"))
        row.addStretch()
        if self.username:
            row.addWidget(label(self.username, "small"))
            row.addWidget(button("Sign out", "link", self.sign_out))
        self.manage_species_button = button("Manage species", "secondary", self.manage_species)
        self.manage_species_button.setEnabled(self.username is not None)
        row.addWidget(self.manage_species_button)
        self.region_choice = ChoiceBox()
        self.region_choice.setAccessibleName("State")
        for code, region in REGIONS.items():
            self.region_choice.addItem(region.name, code)
            if code != "MA":
                self.region_choice.setItemData(self.region_choice.count() - 1, "Experimental regional models", Qt.ItemDataRole.ToolTipRole)
        row.addWidget(self.region_choice)
        nav.setFixedHeight(76)
        return nav

    def build_hero(self):
        hero = QWidget()
        row = QHBoxLayout(hero)
        row.setContentsMargins(0, 32, 0, 8)
        text = QVBoxLayout()
        text.setSpacing(12)
        text.addWidget(label("WildLocate", "eyebrow"))
        text.addWidget(label("Find where wildlife can thrive.", "hero", True))
        description = label("Wild-Locate uses species observations and environmental data to estimate how suitable a location is as habitat for wildlife.", "description", True)
        text.addWidget(description)
        row.addLayout(text, 1)
        return hero

    def build_inputs(self):
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(26, 25, 26, 25)
        layout.setSpacing(10)
        layout.addWidget(label("Explore a location", "heading"))
        layout.addSpacing(3)
        species_label = label("CHOOSE A SPECIES", "step")
        layout.addWidget(species_label)
        self.species = ChoiceBox()
        self.species.addItems(available_species(self.region, username=self.username))
        self.species.setCurrentText("North American River Otter")
        species_label.setBuddy(self.species)
        self.species.setAccessibleName("Species")
        self.species.setToolTip("Choose a species with an enabled model. Add models in Manage species.")
        layout.addWidget(self.species)
        layout.addSpacing(3)
        layout.addWidget(label("CHOOSE A LOCATION", "step"))
        self.location_map = LocationMap()
        self.selected_location = label("", "fieldLabel", True)
        self.selected_location.setAccessibleName("Selected coordinates")
        self.selected_location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.selected_location)
        self.manual_coordinates = Disclosure("Enter coordinates manually", "coordinates")
        self.manual_coordinates.body_layout.setContentsMargins(0, 6, 0, 6)
        coordinate_layout = QHBoxLayout()
        coordinate_layout.setSpacing(12)
        self.latitude = QLineEdit("42.3718")
        self.longitude = QLineEdit("-72.2820")
        for name, field, placeholder in (("Latitude", self.latitude, "e.g. 42.3718"), ("Longitude", self.longitude, "e.g. -72.2820")):
            column = QVBoxLayout()
            column.setSpacing(7)
            caption = label(name, "fieldLabel")
            caption.setBuddy(field)
            field.setAccessibleName(name)
            field.setPlaceholderText(placeholder)
            field.setMinimumWidth(0)
            field.setMaxLength(24)
            field.setToolTip(f"{name} in decimal degrees (WGS 84)")
            column.addWidget(caption)
            column.addWidget(field)
            coordinate_layout.addLayout(column, 1)
        self.manual_coordinates.body_layout.addLayout(coordinate_layout)
        self.coordinate_note = label("Decimal degrees · Massachusetts", "small", True)
        self.manual_coordinates.body_layout.addWidget(self.coordinate_note)
        self.example_button = button("↗  Use example coordinates", "link", self.use_example)
        self.example_button.setToolTip("Fills an example location. Select Analyze Habitat to get a real prediction.")
        self.manual_coordinates.body_layout.addWidget(self.example_button)
        layout.addWidget(self.manual_coordinates)
        if self.location_map.view is None:
            self.manual_coordinates.set_expanded(True)
        options = QHBoxLayout()
        options.setSpacing(10)
        mode_column = QVBoxLayout()
        mode_column.addWidget(label("ANALYSIS", "step"))
        self.analysis_type = ChoiceBox()
        self.analysis_type.setAccessibleName("Analysis type")
        self.analysis_type.addItem("Point analysis", "point")
        self.analysis_type.addItem("Regional analysis", "regional")
        self.analysis_type.setObjectName("compactChoice")
        mode_column.addWidget(self.analysis_type)
        options.addLayout(mode_column, 2)
        self.radius_controls = QWidget()
        radius_layout = QVBoxLayout(self.radius_controls)
        radius_layout.setContentsMargins(0, 0, 0, 0)
        radius_layout.addWidget(label("RADIUS", "step"))
        self.radius_choice = ChoiceBox()
        self.radius_choice.setAccessibleName("Analysis radius")
        for radius in (10, 25, 50):
            self.radius_choice.addItem(f"{radius} km", radius)
        self.radius_choice.setCurrentIndex(1)
        self.radius_choice.setObjectName("compactChoice")
        radius_layout.addWidget(self.radius_choice)
        self.radius_choice.setToolTip("81 sample points; spacing grows with radius.")
        options.addWidget(self.radius_controls, 1)
        layout.addLayout(options)
        self.radius_controls.hide()
        self.error = label("", "error", True)
        self.error.setAccessibleName("Analysis error")
        layout.addWidget(self.error)
        self.error.hide()
        layout.addSpacing(8)
        self.analyze_button = button("Analyze Habitat   →", "primary", self.analyze)
        self.analyze_button.setToolTip("Analyze the selected species and location (Ctrl+Enter)")
        layout.addWidget(self.analyze_button)
        self.cancel_button = button("Cancel analysis", "secondary", self.client.cancel)
        layout.addWidget(self.cancel_button)
        self.cancel_button.hide()
        self.input_note = label("Your analysis runs locally on this computer.", "small", True)
        self.input_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.input_note)
        layout.addStretch(1)
        return card

    def build_results(self):
        card = QFrame()
        card.setObjectName("card")
        card.setMinimumHeight(474)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(12)
        row = QHBoxLayout()
        row.addWidget(label("HABITAT ASSESSMENT", "eyebrow"))
        row.addStretch()
        self.result_status = label("AWAITING ANALYSIS", "small")
        row.addWidget(self.result_status)
        layout.addLayout(row)
        layout.addWidget(self.location_map)
        self.stack = QStackedWidget()
        self.empty_page = QWidget()
        empty_layout = QVBoxLayout(self.empty_page)
        empty_layout.setContentsMargins(12, 10, 12, 0)
        empty_layout.setSpacing(14)
        empty_layout.addStretch()
        heading = label("A landscape of possibility.", "emptyHeading", True)
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(heading)
        self.empty_text = label("Choose a species and a location to discover how the surrounding habitat compares.", "muted", True)
        self.empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_text.setFixedWidth(390)
        self.empty_text.setMinimumHeight(48)
        empty_layout.addWidget(self.empty_text, 0, Qt.AlignmentFlag.AlignHCenter)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setMaximumWidth(240)
        self.progress.setAccessibleName("Habitat analysis in progress")
        empty_layout.addWidget(self.progress, 0, Qt.AlignmentFlag.AlignHCenter)
        self.progress.hide()
        self.elapsed = label("", "small")
        self.elapsed.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.elapsed)
        empty_layout.addStretch()
        self.stack.addWidget(self.empty_page)

        self.result_page = QWidget()
        result_layout = QVBoxLayout(self.result_page)
        result_layout.setContentsMargins(0, 9, 0, 0)
        result_layout.setSpacing(10)
        self.result_species = label("", "heading", True)
        self.result_location = label("", "muted")
        self.result_location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        result_layout.addWidget(self.result_species)
        result_layout.addWidget(self.result_location)
        result_layout.addSpacing(5)
        self.category = label("", "category")
        result_layout.addWidget(self.category, 0, Qt.AlignmentFlag.AlignLeft)
        number_row = QHBoxLayout()
        self.percentile = label("", "percentile")
        number_row.addWidget(self.percentile)
        number_row.addWidget(label("percentile", "description"), 0, Qt.AlignmentFlag.AlignBottom)
        number_row.addStretch()
        result_layout.addLayout(number_row)
        self.gauge = SuitabilityGauge()
        result_layout.addWidget(self.gauge)
        self.interpretation = label("", "muted", True)
        result_layout.addWidget(self.interpretation)
        result_layout.addSpacing(5)
        result_layout.addWidget(divider())
        metadata = QHBoxLayout()
        metadata.setSpacing(18)
        self.score = label("", "subheading")
        self.model = label("", "subheading", True)
        self.observations = label("", "subheading")
        for caption, value in (("Relative suitability score", self.score), ("Model", self.model), ("Training observations", self.observations)):
            column = QVBoxLayout()
            column.setSpacing(5)
            column.addWidget(label(caption, "small", True))
            column.addWidget(value)
            metadata.addLayout(column, 1)
        result_layout.addLayout(metadata)
        self.stack.addWidget(self.result_page)
        self.area_page = QWidget()
        area_layout = QVBoxLayout(self.area_page)
        area_layout.setContentsMargins(0, 8, 0, 0)
        area_layout.setSpacing(10)
        self.area_heading = label("", "heading", True)
        self.area_summary = label("", "muted", True)
        area_layout.addWidget(self.area_heading)
        area_layout.addWidget(self.area_summary)
        legend = QHBoxLayout()
        legend.setSpacing(10)
        for name, color in (("Very low", "#b5423a"), ("Low", "#d88735"), ("Moderate", "#d5bb45"), ("High", "#80a952"), ("Very high", "#286648"), ("No data", "#858585")):
            key = label(f"● {name}", "small")
            key.setStyleSheet(f"color: {color}; font-size: 10px;")
            legend.addWidget(key)
        legend.addStretch()
        area_layout.addLayout(legend)
        self.area_details = Disclosure("View all scores", "coordinates")
        self.area_details.body_layout.setContentsMargins(0, 8, 0, 0)
        self.area_table = QTableWidget(0, 5)
        self.area_table.setHorizontalHeaderLabels(["Latitude", "Longitude", "Score", "Percentile", "Suitability"])
        self.area_table.setAccessibleName("Regional suitability grid results")
        self.area_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.area_table.verticalHeader().hide()
        self.area_table.horizontalHeader().setStretchLastSection(True)
        self.area_table.setObjectName("areaTable")
        self.area_table.setShowGrid(False)
        self.area_table.setAlternatingRowColors(True)
        self.area_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.area_table.setFrameShape(QFrame.Shape.NoFrame)
        self.area_table.verticalHeader().setDefaultSectionSize(26)
        self.area_table.verticalHeader().setMinimumSectionSize(24)
        self.area_table.setFixedHeight(210)
        self.area_details.body_layout.addWidget(self.area_table)
        area_layout.addWidget(self.area_details)
        area_layout.addStretch()
        self.stack.addWidget(self.area_page)
        self.stack.currentChanged.connect(self.fit_result_height)
        self.area_details.toggle.toggled.connect(self.fit_result_height)
        self.fit_result_height()
        layout.addWidget(self.stack)
        layout.addWidget(label("The suitability score is relative and does not represent the probability that the species is currently present.", "notice", True))
        self.export_button = button("↓  Export assessment as JSON", "link", self.export_result)
        self.export_button.hide()
        layout.addWidget(self.export_button)
        return card

    def fit_result_height(self, *_):
        page = self.stack.currentWidget()
        page.layout().activate()
        self.stack.setFixedHeight(page.sizeHint().height())

    def build_methodology(self):
        info = Disclosure("Assessment Info", "methodology")
        info.body_layout.addWidget(label("The existing species-specific machine-learning model combines iNaturalist species observations with environmental conditions at your selected location.", "muted", True))
        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(16)
        entries = (
            ("Observations", "iNaturalist species observations used in model training."),
            ("Land & terrain", "NLCD land cover and impervious surface; USGS elevation and derived terrain conditions."),
            ("Regional features", "Massachusetts uses local water and road distances. Florida and Arizona use a separate raster-only model with shrubland, grassland and other land-cover types."),
            ("Relative suitability", "Percentiles compare this location's score with the species' comparison locations. They are not a measure of model confidence."),
        )
        for index, (title, description) in enumerate(entries):
            column = QVBoxLayout()
            column.setSpacing(7)
            column.addWidget(label(title, "fieldLabel"))
            column.addWidget(label(description, "muted", True))
            grid.addLayout(column, index // 2, index % 2)
        info.body_layout.addLayout(grid)
        info.body_layout.addWidget(label("Percentile guide: 0–19 Very Low · 20–39 Low · 40–59 Moderate · 60–79 High · 80–100 Very High", "small", True))
        return info

    def responsive_layout(self):
        narrow = self.width() < 1000
        self.cards.setDirection(QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight)
        self.input_card.setMinimumWidth(0 if narrow else 370)
        self.input_card.setMaximumWidth(16777215 if narrow else 400)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "cards"):
            self.responsive_layout()

    def change_region(self):
        self.client.close()
        self.region = self.region_choice.currentData()
        region = get_region(self.region)
        self.refresh_species()
        self.coordinate_note.setText(f"Decimal degrees · {region.name}")
        self.location_map.region_center = region.center
        self.use_example()
        self.empty_text.setText("Choose a species and location, then select Analyze Habitat.")
        if not available_species(self.region, username=self.username):
            self.empty_text.setText(f"No enabled models for {region.name} yet. Open Manage species to train and review a model.")
            self.input_note.setText("Suggested mammals: " + ", ".join(region.examples))
        elif self.region != "MA":
            self.input_note.setText("Experimental regional models · first analysis may download environmental tiles.")

    def use_example(self):
        lat, lon = (42.28, -71.35) if self.species.currentText() == "Red Fox" else get_region("MA").center
        if self.region != "MA":
            lat, lon = get_region(self.region).center
        self.latitude.setText(f"{lat:.4f}")
        self.longitude.setText(f"{lon:.4f}")
        self.sync_map(recenter=True)
        self.input_note.setText("Example coordinates loaded. Ready to analyze.")

    def manage_species(self):
        if self.client.busy or self.username is None:
            return
        dialog = SpeciesManager(self, region=self.region, username=self.username)
        dialog.models_changed.connect(self.refresh_species)
        dialog.exec()
        self.refresh_species()
        dialog.deleteLater()

    def refresh_species(self):
        selected = self.species.currentText()
        with QSignalBlocker(self.species):
            self.species.clear()
            self.species.addItems(available_species(self.region, username=self.username))
            self.species.setPlaceholderText("No enabled models — open Manage species")
            if self.species.findText(selected) >= 0:
                self.species.setCurrentText(selected)
        # A new model for the same species also invalidates the previous result.
        self.inputs_changed()
        self.analyze_button.setEnabled(bool(self.species.count()))
        if self.species.count() and self.result is None:
            self.empty_text.setText("Choose a species and location, then select Analyze Habitat.")

    def map_selected(self, latitude, longitude):
        if self.client.busy:
            return
        with QSignalBlocker(self.latitude), QSignalBlocker(self.longitude):
            self.latitude.setText(f"{latitude:.6f}")
            self.longitude.setText(f"{longitude:.6f}")
        self.inputs_changed()
        self.sync_map()

    def sync_map(self, *_args, recenter=False):
        try:
            latitude, longitude = float(self.latitude.text()), float(self.longitude.text())
            if not (math.isfinite(latitude) and math.isfinite(longitude) and -90 <= latitude <= 90 and -180 <= longitude <= 180):
                raise ValueError
        except ValueError:
            self.selected_location.setText("Choose a point on the map or enter valid coordinates.")
            self.location_map.set_location()
            return
        self.selected_location.setText(coordinates(latitude, longitude))
        self.location_map.set_location(latitude, longitude, recenter=recenter)

    def analysis_changed(self):
        regional = self.analysis_type.currentData() == "regional"
        self.radius_controls.setVisible(regional)
        self.inputs_changed()
        self.location_map.set_area(self.radius_choice.currentData() if regional else None)

    def inputs_changed(self):
        self.location_map.set_area(self.radius_choice.currentData() if self.analysis_type.currentData() == "regional" else None)
        self.error.hide()
        for field in (self.species, self.latitude, self.longitude):
            field.setProperty("invalid", False)
            field.style().unpolish(field)
            field.style().polish(field)
        if self.result is not None:
            self.result = None
            self.stack.setCurrentWidget(self.empty_page)
            self.environment.hide()
            self.insights.hide()
            self.export_button.hide()
            self.empty_text.setText("Your selection has changed. Analyze this location to see a new assessment.")
            self.result_status.setText("AWAITING ANALYSIS")
        self.input_note.setText("Your analysis runs locally on this computer." if self.region == "MA" else "Analysis runs locally; uncached environmental tiles need an internet connection.")

    def read_coordinates(self):
        values = []
        errors = []
        first_invalid = None
        for name, field, limit in (("Latitude", self.latitude, 90), ("Longitude", self.longitude, 180)):
            try:
                value = float(field.text().strip())
                if not math.isfinite(value) or not -limit <= value <= limit:
                    raise ValueError
                values.append(value)
            except ValueError:
                field.setProperty("invalid", True)
                field.style().unpolish(field)
                field.style().polish(field)
                errors.append(f"{name} must be a number between −{limit} and {limit}.")
                first_invalid = first_invalid or field
        if errors:
            self.manual_coordinates.set_expanded(True)
            self.error.setText("\n".join(errors))
            self.error.show()
            first_invalid.setFocus()
            return None
        return values

    def analyze(self):
        if self.client.busy:
            return
        species = self.species.currentText()
        if species not in available_species(self.region, username=self.username):
            self.species.setProperty("invalid", True)
            self.species.style().unpolish(self.species)
            self.species.style().polish(self.species)
            self.error.setText("Choose an available species from the dropdown, or enable a model in Manage species.")
            self.error.show()
            self.species.setFocus()
            return
        values = self.read_coordinates()
        if values is None:
            return
        self.error.hide()
        self.result = None
        self.location_map.set_area(self.radius_choice.currentData() if self.analysis_type.currentData() == "regional" else None)
        self.insights.hide()
        self.environment.hide()
        self.export_button.hide()
        self.stack.setCurrentWidget(self.empty_page)
        self.empty_text.setText("Reading local environmental data and evaluating the species model." if self.region == "MA" else "Loading environmental tiles and evaluating the regional model. Missing tiles will download first.")
        self.result_status.setText("ANALYSIS IN PROGRESS")
        self.set_busy(True)
        self._started_at = time.monotonic()
        self.update_elapsed()
        self.timer.start()
        radius = self.radius_choice.currentData() if self.analysis_type.currentData() == "regional" else None
        if radius is not None:
            self.empty_text.setText("Evaluating 81 grid points in the surrounding area. Missing environmental tiles may need to download. You can cancel at any time.")
        self.client.analyze(species, *values, region=self.region, radius_km=radius)

    def set_busy(self, busy):
        for widget in (self.species, self.latitude, self.longitude, self.location_map, self.example_button, self.analyze_button, self.manage_species_button, self.region_choice, self.analysis_type, self.radius_choice):
            widget.setEnabled(not busy)
        self.manage_species_button.setEnabled(not busy and self.username is not None)
        self.analyze_button.setEnabled(not busy and bool(self.species.count()))
        self.cancel_button.setVisible(busy)
        self.progress.setVisible(busy)
        self.analyze_button.setText("Analyzing habitat…" if busy else "Analyze Habitat   →")
        if not busy:
            self.timer.stop()
            self.elapsed.setText("")

    def update_elapsed(self):
        seconds = int(time.monotonic() - self._started_at)
        self.elapsed.setText(f"{seconds}s elapsed · first analysis may take longer")

    def show_result(self, result):
        self.set_busy(False)
        self.result = result
        if result.get("analysis_type") == "regional":
            self.show_area_result(result)
            return
        self.result_species.setText(result["species"])
        self.result_location.setText(coordinates(result["latitude"], result["longitude"]))
        self.category.setText(f"{result['category'].upper()} HABITAT SUITABILITY")
        self.percentile.setText(ordinal(result["percentile"]))
        self.gauge.set_percentile(result["percentile"])
        self.interpretation.setText(f"This location received a higher habitat-suitability score than approximately {result['percentile']}% of comparison locations for this species.")
        self.score.setText(f"{result['score']:.3f}")
        self.model.setText(result["model"])
        self.observations.setText(f"{result['training_observations']:,}")
        self.result_status.setText("ASSESSMENT COMPLETE")
        self.stack.setCurrentWidget(self.result_page)
        while self.feature_table.count():
            item = self.feature_table.takeAt(0)
            item.widget().deleteLater()
        for row, (name, value) in enumerate(result["features"].items()):
            title, formatted = feature_display(name, value)
            self.feature_table.addWidget(label(title, "muted", True), row, 0)
            value_label = label(formatted, "fieldLabel")
            value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            self.feature_table.addWidget(value_label, row, 1)
        self.feature_table.setColumnStretch(0, 1)
        self.environment.set_expanded(False)
        self.environment.show()
        self.show_insights(result.get("insights", {}))
        self.export_button.show()
        self.input_note.setText("Assessment complete. Explore another location.")

    def show_area_result(self, result):
        self.area_heading.setText(f"{result['species']} · {result['radius_km']} km radius")
        summary = (
            f"Centered at {coordinates(result['latitude'], result['longitude'])}. "
            f"{result['evaluated_points']} points scored; {result['unavailable_points']} unavailable. "
            f"Grid spacing: {result['grid_spacing_km']:g} km. "
        )
        if result['mean_score'] is None:
            summary += "No grid points could be evaluated. Try another location or a smaller radius."
        else:
            summary += f"Mean score of evaluated points: {result['mean_score']:.3f}. "
        summary += f" Model: {result['model']}; {result['training_observations']:,} training observations. This is a sampled grid, not continuous habitat coverage."
        if result['mean_score'] is None:
            brief = "No grid points could be evaluated. Try another location or a smaller radius."
        else:
            brief = f"{result['evaluated_points']} points scored · Mean score {result['mean_score']:.3f} · {result['grid_spacing_km']:g} km spacing"
            if result['unavailable_points']:
                brief += f" · {result['unavailable_points']} unavailable"
        self.area_summary.setText(brief)
        self.area_summary.setToolTip(summary)
        self.area_details.set_expanded(False)
        self.interpretation.setText(summary)
        self.area_table.setRowCount(len(result['points']))
        for row, point in enumerate(result['points']):
            ok = point['status'] == 'ok'
            values = [f"{point['latitude']:.5f}", f"{point['longitude']:.5f}",
                      f"{point['score']:.3f}" if ok else "—",
                      str(point['percentile']) if ok else "—",
                      point['category'] if ok else "Unavailable"]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if not ok:
                    item.setToolTip(point.get('reason', 'Environmental data unavailable'))
                self.area_table.setItem(row, col, item)
        self.area_table.resizeColumnsToContents()
        self.stack.setCurrentWidget(self.area_page)
        self.fit_result_height()
        self.environment.hide()
        self.insights.hide()
        self.export_button.show()
        self.result_status.setText("REGIONAL ASSESSMENT COMPLETE" if result['evaluated_points'] else "NO COVERAGE")
        self.location_map.set_area(result['radius_km'], result['points'])
        self.input_note.setText("Select a map point to see its score.")

    def show_insights(self, insights):
        while self.insights.body_layout.count():
            item = self.insights.body_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.insights.body_layout.addWidget(InsightsPanel(insights))
        self.insights.set_expanded(True)
        self.insights.show()

    def show_error(self, message):
        self.set_busy(False)
        self.error.setText(message)
        self.error.show()
        self.empty_text.setText("We couldn't complete this assessment. Review the message and try again.")
        self.result_status.setText("ANALYSIS UNAVAILABLE")

    def cancel_if_busy(self):
        if self.client.busy:
            self.client.cancel()

    def cancelled(self):
        self.set_busy(False)
        self.empty_text.setText("Analysis cancelled. Your selected species and coordinates are ready when you are.")
        self.result_status.setText("AWAITING ANALYSIS")

    def export_result(self):
        if self.result is None:
            return
        filename = f"wild-locate-{self.result['species'].lower().replace(' ', '-')}.json"
        path, _ = QFileDialog.getSaveFileName(self, "Export habitat assessment", filename, "JSON files (*.json)")
        if not path:
            return
        payload = dict(self.result)
        payload["interpretation"] = self.interpretation.text()
        payload["note"] = "The suitability score is relative and does not represent the probability that the species is currently present."
        file = QSaveFile(path)
        data = (json.dumps(payload, indent=2, allow_nan=False) + "\n").encode("utf-8")
        if not file.open(QIODevice.OpenModeFlag.WriteOnly) or file.write(data) != len(data) or not file.commit():
            self.error.setText("The assessment could not be saved. Choose a writable folder and try again.")
            self.error.show()
            return
        self.input_note.setText("Assessment exported successfully.")

    def sign_out(self):
        self.signed_out = True
        self.close()

    def closeEvent(self, event):
        self.client.close()
        self.location_map.shutdown()
        event.accept()


def create_application(argv=None):
    app = QApplication((sys.argv if argv is None else argv) or ["wild-locate"])
    if app.platformName() == "offscreen" and sys.platform == "win32":
        import os
        font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for filename in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "seguisym.ttf", "georgia.ttf"):
            QFontDatabase.addApplicationFont(str(font_dir / filename))
    app.setApplicationName("Wild-Locate")
    app.setOrganizationName("Wild-Locate")
    app.setStyle("Fusion")
    app.setPalette(light_palette())
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(STYLESHEET)
    return app


def main():
    app = create_application()
    while True:
        login = LoginDialog()
        if login.exec() != QDialog.DialogCode.Accepted:
            break
        window = MainWindow(username=login.username)
        window.show()
        app.exec()
        if not window.signed_out:
            break


if __name__ == "__main__":
    main()
