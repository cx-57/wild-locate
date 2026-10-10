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
from wildlocate.gui.views import ResultViewMixin

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

class MainWindow(ResultViewMixin, QMainWindow):
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
        # Clear the old result when the species model changes
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
