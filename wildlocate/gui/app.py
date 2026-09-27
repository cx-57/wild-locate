"""Wild-Locate desktop interface."""

import json
import math
from pathlib import Path
import sys
import time

from PyQt6.QtCore import (
    QIODevice, QObject, QPointF, QProcess, QRectF, QSaveFile, QSignalBlocker,
    Qt, QTimer, QUrl, pyqtSignal, pyqtSlot,
)
from PyQt6.QtGui import (
    QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QKeySequence,
    QPainter, QPainterPath, QPalette, QPen, QPixmap, QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QBoxLayout, QComboBox, QDialog, QFileDialog,
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QStyledItemDelegate,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from wildlocate.core.modeling import new_job_id
from wildlocate.core.registry import (
    authenticate, available_models, available_species, cleanup_job, delete_model,
    enable_model, list_models, normalize_username,
)
from wildlocate.core.regional import REGIONS, get_region
# Application palette and stylesheet

def light_palette():
    """Keep native Qt control parts consistent with the light stylesheet."""
    palette = QPalette(QColor("#f5f6f2"))
    for role, color in {
        "Window": "#f5f6f2", "WindowText": "#233c34", "Base": "#ffffff",
        "AlternateBase": "#fbfcf9", "Text": "#233c34", "Button": "#fbfcf9",
        "ButtonText": "#233c34", "Highlight": "#e8efdf", "HighlightedText": "#193e30",
        "PlaceholderText": "#718075", "Light": "#ffffff", "Midlight": "#e6ebe3",
        "Mid": "#d4ded2", "Dark": "#9bad98", "Shadow": "#9bad98",
    }.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#8a958d"))
    return palette


STYLESHEET = """
QWidget { font-family: 'Segoe UI'; font-size: 13px; color: #233c34; }
QMainWindow, QScrollArea, QWidget#canvas, QWidget#page { background: #f5f6f2; }
QScrollArea { border: 0; }
QWidget#nav { background: #f5f6f2; border-bottom: 1px solid #dde3da; }
QLabel { background: transparent; border: none; }
QLabel#brand { font-size: 22px; font-weight: 650; letter-spacing: -0.7px; }
QLabel#eyebrow { font-size: 10px; font-weight: 600; letter-spacing: 2px; color: #627b6e; }
QLabel#hero { font-family: 'Georgia'; font-size: 41px; color: #193e30; }
QLabel#description { font-size: 14px; color: #66756c; }
QLabel#heading { font-size: 20px; font-weight: 600; letter-spacing: -0.4px; }
QLabel#subheading { font-size: 15px; font-weight: 600; }
QLabel#muted { color: #69776e; font-size: 12px; }
QLabel#small { color: #718075; font-size: 11px; }
QLabel#fieldLabel { font-size: 12px; font-weight: 600; color: #42594d; }
QLabel#step { font-size: 10px; font-weight: 600; letter-spacing: 1.3px; color: #77867a; }
QLabel#pill { background: #e7eee5; color: #3e6550; border: 1px solid #d8e4d5; border-radius: 12px; padding: 5px 11px; font-size: 10px; font-weight: 600; letter-spacing: 0.8px; }
QLabel#category { background: #e9f0dd; color: #426139; border-radius: 10px; padding: 6px 11px; font-size: 10px; font-weight: 600; letter-spacing: 1px; }
QLabel#error { color: #963f32; background: #fcf0eb; border: 1px solid #efd7ce; border-radius: 8px; padding: 12px; }
QLabel#notice { color: #607165; background: #f0f3ec; border-radius: 8px; padding: 12px; font-size: 11px; }
QLabel#percentile { color: #204b37; font-size: 67px; font-weight: 500; letter-spacing: -3px; }
QLabel#emptyHeading { color: #355944; font-family: 'Georgia'; font-size: 27px; }
QFrame#card { background: #ffffff; border: 1px solid #dfe5dc; border-radius: 14px; }
QFrame#divider { background: #e6ebe3; border: none; min-height: 1px; max-height: 1px; }
QFrame#environment, QFrame#methodology { background: #ffffff; border: 1px solid #dfe5dc; border-radius: 12px; }
QLineEdit, QComboBox { background: #fbfcf9; border: 1px solid #d4ded2; border-radius: 7px; padding: 12px 11px; min-height: 20px; selection-background-color: #285a40; }
QLineEdit:hover, QComboBox:hover { border-color: #9bad98; }
QLineEdit:focus, QComboBox:focus { border: 2px solid #477c54; padding: 11px 10px; }
QLineEdit:disabled, QComboBox:disabled { color: #8a958d; background: #f4f6f1; border-color: #e3e8df; }
QLineEdit[invalid="true"], QComboBox[invalid="true"] { border: 1px solid #b75742; background: #fff8f4; }
QComboBox { padding-right: 32px; }
QComboBox:focus { padding-right: 31px; }
QComboBox#compactChoice { padding: 7px 26px 7px 8px; min-height: 18px; font-size: 12px; }
QComboBox#compactChoice:focus { padding: 6px 25px 6px 7px; }
QTableWidget#areaTable { border: none; background: #ffffff; alternate-background-color: #f7f9f5; font-size: 11px; selection-background-color: #e8efdf; }
QTableWidget#areaTable::item { padding: 3px 8px; border: none; }
QTableWidget#areaTable QHeaderView::section { background: #f2f5ef; color: #69776e; border: none; border-bottom: 1px solid #e3e9df; padding: 6px 8px; font-size: 10px; font-weight: 500; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 28px; border: none; background: transparent; }
QComboBox::down-arrow { image: none; }
QFrame#comboPopup { background: #ffffff; border: none; }
QAbstractItemView#comboOptions { background: #ffffff; color: #233c34; border: 1px solid #d4ded2; padding: 0; selection-background-color: #e8efdf; selection-color: #193e30; outline: none; }
QAbstractItemView#comboOptions::item { min-height: 22px; padding: 8px 12px; }
QPushButton { background: transparent; border: 1px solid transparent; border-radius: 7px; padding: 9px 12px; font-weight: 500; }
QPushButton:hover { background: #eaf0e5; }
QPushButton:focus { border: 1px solid #477c54; }
QPushButton#primary { background: #254f38; color: #ffffff; font-size: 14px; font-weight: 600; padding: 14px 16px; }
QPushButton#primary:hover { background: #326647; }
QPushButton#primary:pressed { background: #193e2a; }
QPushButton#primary:disabled { color: #d4dfce; background: #718974; }
QPushButton#secondary { border: 1px solid #d5dfcf; color: #42634b; }
QPushButton#link { color: #47724f; font-size: 11px; padding: 5px 0px; text-align: left; }
QPushButton#accordion { text-align: left; padding: 18px 21px; font-size: 13px; font-weight: 600; }
QFrame#coordinates { background: transparent; border: none; }
QFrame#coordinates QPushButton#accordion { color: #47724f; font-size: 11px; padding: 5px 0; }
QPushButton:disabled { color: #8e9a8b; }
QProgressBar { background: #e5ecdf; border: none; border-radius: 2px; max-height: 4px; min-height: 4px; }
QProgressBar::chunk { background: #73945b; border-radius: 2px; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 4px 0px; }
QScrollBar::handle:vertical { background: #c6d1bf; border-radius: 4px; min-height: 35px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QToolTip { background: #234633; color: #ffffff; border: none; padding: 8px; }
QDialog, QTabWidget::pane { background: #f5f6f2; }
QTabWidget::pane { border: 1px solid #dfe5dc; border-radius: 7px; }
QTabBar::tab { padding: 10px 16px; background: #e7eee5; color: #42594d; }
QTabBar::tab:selected { background: #ffffff; color: #193e30; }
QListWidget, QPlainTextEdit { background: #ffffff; border: 1px solid #d4ded2; border-radius: 7px; padding: 6px; }
QListWidget::item { padding: 8px; }
QListWidget::item:selected { background: #e8efdf; color: #193e30; }
"""


# Display formatting

def ordinal(value):
    suffix = "th" if 10 <= value % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def coordinates(latitude, longitude):
    return f"{abs(latitude):.4f}° {'N' if latitude >= 0 else 'S'}  /  {abs(longitude):.4f}° {'E' if longitude >= 0 else 'W'}"


def feature_display(name, value):
    radius = "250 m" if name.endswith("_250m") else "1 km"
    labels = {
        "forest_fraction": "Forest cover",
        "shrubland_fraction": "Shrubland cover",
        "grassland_fraction": "Grassland cover",
        "barren_fraction": "Bare ground",
        "cropland_fraction": "Agricultural land",
        "wetland_fraction": "Wetland cover",
        "developed_fraction": "Developed land",
        "open_water_fraction": "Open water",
        "mean_impervious": "Impervious surface",
        "mean_slope": "Average slope",
        "terrain_ruggedness": "Terrain ruggedness",
    }
    label = {
        "elevation_m": "Elevation",
        "distance_to_water_m": "Distance to nearest water",
        "distance_to_road_m": "Distance to nearest road",
    }.get(name)
    if label is None:
        prefix = name.rsplit("_", 1)[0]
        label = f"{labels[prefix]} within {radius}" if prefix in labels else name.replace("_", " ").capitalize()
    if not math.isfinite(value):
        return label, "Unavailable"
    if "_fraction_" in name:
        formatted = f"{value * 100:.1f}%"
    elif name.startswith("mean_impervious"):
        formatted = f"{value:.1f}%"
    elif name.startswith("mean_slope"):
        formatted = f"{value:.1f}°"
    elif name.endswith("_m") or name.startswith("terrain_ruggedness"):
        formatted = f"{value:,.0f} m"
    else:
        formatted = f"{value:,.2f}"
    return label, formatted


# Reusable widgets

def label(text, role="", wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setObjectName(role)
    widget.setWordWrap(wrap)
    return widget


def divider():
    widget = QFrame()
    widget.setObjectName("divider")
    return widget


class ChoiceBox(QComboBox):
    """Use the same themed popup for state and species choices."""

    def __init__(self, parent=None):
        super().__init__(parent)
        popup = self.view()
        popup.setObjectName("comboOptions")
        popup.setItemDelegate(QStyledItemDelegate(popup))
        popup.setTextElideMode(Qt.TextElideMode.ElideNone)
        container = popup.window()
        container.setObjectName("comboPopup")
        if isinstance(container, QFrame):
            container.setFrameShape(QFrame.Shape.NoFrame)

    def showPopup(self):
        popup = self.view()
        popup.ensurePolished()
        popup.setMinimumWidth(popup.sizeHintForColumn(0) + 2 * popup.frameWidth())
        super().showPopup()

    def paintEvent(self, event):
        super().paintEvent(event)
        # Draw a chevron explicitly: styling Qt's drop-down removes its native arrow.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#607165" if self.isEnabled() else "#8a958d"), 1.5,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        x = 16 if self.layoutDirection() == Qt.LayoutDirection.RightToLeft else self.width() - 16
        y = self.height() / 2
        painter.drawPolyline(QPointF(x - 4, y - 2), QPointF(x, y + 2), QPointF(x + 4, y - 2))


def draw_mark(painter, size):
    painter.save()
    painter.scale(size / 40, size / 40)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#254f38"))
    painter.drawRoundedRect(QRectF(0, 0, 40, 40), 11, 11)
    path = QPainterPath(QPointF(12, 28))
    path.cubicTo(5, 12, 22, 10, 30, 9)
    path.cubicTo(31, 24, 25, 32, 12, 28)
    painter.setBrush(QColor("#d4e2b5"))
    painter.drawPath(path)
    painter.setPen(QPen(QColor("#254f38"), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(QPointF(12, 29), QPointF(24, 17))
    painter.restore()


def app_icon():
    pixmap = QPixmap(80, 80)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    draw_mark(painter, 80)
    painter.end()
    return QIcon(pixmap)


class BrandMark(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(36, 36)

    def paintEvent(self, event):
        painter = QPainter(self)
        draw_mark(painter, 36)


class SuitabilityGauge(QWidget):
    COLORS = ("#e1e6d9", "#c9d6af", "#a5bb7c", "#708f4c", "#345e3c")

    def __init__(self):
        super().__init__()
        self.percentile = None
        self.setMinimumHeight(72)
        self.setToolTip("Percentile categories: 0–19 Very Low; 20–39 Low; 40–59 Moderate; 60–79 High; 80–100 Very High.")

    def set_percentile(self, percentile):
        self.percentile = percentile
        self.setAccessibleName(f"Habitat suitability: {percentile}th percentile on a scale of 0 to 100")
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width() - 12
        for index, color in enumerate(self.COLORS):
            x = 6 + width * index / 5
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(x, 19, width / 5 - 3, 9), 3, 3)
            p.setPen(QColor("#758170"))
            font = QFont("Segoe UI")
            font.setPixelSize(10)
            p.setFont(font)
            p.drawText(QRectF(x - 2, 37, width / 5, 15), Qt.AlignmentFlag.AlignCenter, ("Very low", "Low", "Moderate", "High", "Very high")[index])
        if self.percentile is not None:
            x = 6 + (width - 3) * self.percentile / 100
            p.setPen(QPen(QColor("#ffffff"), 3))
            p.setBrush(QColor("#254f38"))
            p.drawEllipse(QPointF(x, 23.5), 7.5, 7.5)
            p.setPen(QPen(QColor("#254f38"), 1))
            p.drawLine(QPointF(x, 3), QPointF(x, 11))


class Disclosure(QFrame):
    def __init__(self, title, role="environment"):
        super().__init__()
        self.setObjectName(role)
        self.title = title
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        self.toggle = QPushButton(f"+   {title}")
        self.toggle.setObjectName("accordion")
        self.toggle.setCheckable(True)
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.toggled.connect(self.set_expanded)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(22, 0, 22, 22)
        self.body_layout.setSpacing(10)
        self.layout.addWidget(self.toggle)
        self.layout.addWidget(self.body)
        self.body.hide()

    def set_expanded(self, expanded):
        self.toggle.setChecked(expanded)
        self.toggle.setText(f"{'−' if expanded else '+'}   {self.title}")
        self.body.setVisible(expanded)


# Habitat insights panel

def text(value, role='muted'):
    widget = label(value, role, True)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def row(layout, title, detail, delta):
    container = QWidget()
    line = QHBoxLayout(container)
    line.setContentsMargins(0, 8, 0, 8)
    line.setSpacing(20)
    copy = QVBoxLayout()
    copy.setSpacing(4)
    copy.addWidget(text(title, 'fieldLabel'))
    copy.addWidget(text(detail, 'small'))
    line.addLayout(copy, 1)
    metric = text(f'{delta:+.3f}\nscore', 'fieldLabel')
    metric.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    metric.setStyleSheet('color: ' + ('#35664b' if delta > 0 else '#94523f' if delta < 0 else '#69776e') + ';')
    line.addWidget(metric)
    layout.addWidget(container)


class InsightsPanel(QWidget):
    def __init__(self, insights):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        if insights.get('error'):
            layout.addWidget(text(insights['error']))
            return
        tabs = QTabWidget()
        layout.addWidget(tabs)
        conditions = QWidget()
        body = QVBoxLayout(conditions)
        body.setContentsMargins(18, 18, 18, 18)
        body.setSpacing(8)
        body.addWidget(text('What’s shaping this score?', 'subheading'))
        body.addWidget(text('These conditions make the score higher or lower than it would be with typical values from other locations.'))
        influences = insights.get('influences', [])
        for heading, sign in [('BRINGING THE SCORE DOWN', -1), ('LIFTING THE SCORE UP', 1)]:
            body.addSpacing(12)
            body.addWidget(text(heading, 'step'))
            selected = sorted((item for item in influences if item['effect'] * sign > .0005),
                              key=lambda item: abs(item['effect']), reverse=True)[:2]
            if not selected:
                body.addWidget(text('Nothing stands out in this group.', 'small'))
            for item in selected:
                title, current = feature_display(item['feature'], item['current'])
                _, reference = feature_display(item['feature'], item['reference'])
                row(body, title, f'Here: {current}   ·   Typical: {reference}', item['effect'])
                body.addWidget(divider())
        body.addStretch()
        tabs.addTab(conditions, 'Local conditions')

        priorities_page = QWidget()
        body = QVBoxLayout(priorities_page)
        body.setContentsMargins(18, 18, 18, 18)
        body.setSpacing(12)
        species = insights.get('species', 'this species')
        body.addWidget(text(f'What matters most in the {species} model?', 'subheading'))
        body.addWidget(text('These are the model’s top three habitat features for this animal.'))
        priorities = insights.get('top_features', [])[:3]
        for rank, item in enumerate(priorities, 1):
            name = item['feature']
            current = insights.get('feature_values', {}).get(name, float('nan'))
            title, formatted = feature_display(name, current)
            card = QFrame()
            card.setObjectName('card')
            content = QHBoxLayout(card)
            content.setContentsMargins(14, 12, 14, 12)
            content.setSpacing(14)
            content.addWidget(text(f'{rank:02d}', 'subheading'))
            copy = QVBoxLayout()
            copy.setSpacing(4)
            copy.addWidget(text(title, 'fieldLabel'))
            description = f'Here: {formatted}'
            coefficient = item.get('coefficient')
            if coefficient is not None and coefficient != 0:
                direction = 'higher' if coefficient > 0 else 'lower'
                description += f' · Higher values tend to give {direction} scores in this model'
            copy.addWidget(text(description, 'small'))
            content.addLayout(copy, 1)
            body.addWidget(card)
        if not priorities:
            body.addWidget(text('This model doesn’t have a feature ranking to show yet.', 'small'))
        body.addStretch()
        tabs.addTab(priorities_page, 'Species priorities')
        layout.addWidget(text('These clues come from the model. They don’t prove what the animal needs or what would improve its habitat.', 'small'))
        method = Disclosure('How we work this out')
        method.body_layout.addWidget(text(
            'We rank features by how much the model relies on them. A feature near the top matters more to its predictions, but that doesn’t mean more of it is always better.\n\n'
            'To check a local condition, we replace it with the middle value from the training locations and leave everything else as it is. The number beside it shows how much higher or lower the original score is. '
            'These comparisons are separate, so their numbers won’t add up to the total score.', 'small'))
        layout.addWidget(method)

# -----------------------------------------------------------------------------




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

# -----------------------------------------------------------------------------





# Habitat prediction

class PredictionClient(QObject):
    succeeded = pyqtSignal(dict)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, parent=None, *, username=None):
        super().__init__(parent)
        self.process = QProcess(self)
        interpreter = Path(sys.executable)
        # A .pyw launch uses pythonw, whose standard streams may be absent.
        # QProcess supplies pipes to the console interpreter without requiring
        # a terminal window; use the sibling from the same virtual environment.
        if interpreter.name.lower() == "pythonw.exe":
            interpreter = interpreter.with_name("python.exe")
        self.process.setProgram(str(interpreter))
        arguments = ["-u", "-m", "wildlocate.core.worker", "predict"]
        if username is not None:
            arguments.extend(["--account", username])
        self.process.setArguments(arguments)
        self.process.started.connect(self.send_pending)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.readyReadStandardError.connect(self.read_error)
        self.process.errorOccurred.connect(self.process_error)
        self.process.finished.connect(self.finished)
        self.busy = False
        self._buffer = b""
        self._pending = None

    def analyze(self, species, latitude, longitude, region="MA", *, radius_km=None):
        if self.busy:
            return
        self.busy = True
        self._pending = {"species": species, "latitude": latitude, "longitude": longitude, "region": region}
        if radius_km is not None:
            self._pending["radius_km"] = radius_km
        self._buffer = b""
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self.process.start()
        else:
            self.send_pending()

    def send_pending(self):
        if self._pending is not None:
            self.process.write((json.dumps(self._pending) + "\n").encode("utf-8"))
            self._pending = None

    def read_output(self):
        self._buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            if not self.busy or not line.strip():
                continue
            self.busy = False
            try:
                response = json.loads(line)
                if "error" in response:
                    self.failed.emit(response["error"])
                else:
                    self.succeeded.emit(response["result"])
            except (ValueError, KeyError, TypeError):
                self.failed.emit("The analysis returned an unreadable response. Please try again.")

    def read_error(self):
        self.process.readAllStandardError()

    def process_error(self, error):
        if self.busy and error == QProcess.ProcessError.FailedToStart:
            self.busy = False
            self._pending = None
            self.failed.emit("The Python analysis process could not start. Ensure wild-locate is installed in your active environment.")

    def finished(self, exit_code, exit_status):
        if self.busy:
            self.busy = False
            self._pending = None
            self.failed.emit("The analysis process stopped unexpectedly. Check the project dependencies and data, then try again.")

    def cancel(self):
        self.close()
        self.cancelled.emit()

    def close(self):
        self.busy = False
        self._pending = None
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(1000)


# Species training

class TrainingClient(QObject):
    event_received = pyqtSignal(dict)
    log = pyqtSignal(str)

    def __init__(self, parent=None, region="MA", *, username=None):
        self.region = region
        self.username = normalize_username(username)
        super().__init__(parent)
        self.process = QProcess(self)
        interpreter = Path(sys.executable)
        if interpreter.name.lower() == "pythonw.exe":
            interpreter = interpreter.with_name("python.exe")
        self.process.setProgram(str(interpreter))
        self.process.started.connect(self.send_pending)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.readyReadStandardError.connect(self.read_log)
        self.process.errorOccurred.connect(self.process_error)
        self.process.finished.connect(self.finished)
        self.busy = False
        self.job_id = None
        self._pending = None
        self._buffer = b""
        self._stopping = False

    def request(self, action, **payload):
        if self.busy:
            return
        self.busy = True
        self._pending = {"action": action, **payload}
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self.job_id = new_job_id()
            self._buffer = b""
            self.process.setArguments(["-u", "-m", "wildlocate.core.worker", "train", "--job-id", self.job_id, "--region", self.region, "--account", self.username])
            self.process.start()
        else:
            self.send_pending()

    def send_pending(self):
        if self._pending is not None:
            self.process.write((json.dumps(self._pending) + "\n").encode("utf-8"))
            self._pending = None

    def read_output(self):
        self._buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            if not line.strip() or self._stopping:
                continue
            try:
                event = json.loads(line)
                if event["event"] != "progress":
                    self.busy = False
                self.event_received.emit(event)
            except (ValueError, KeyError, TypeError):
                self.close()
                self.event_received.emit({"event": "error", "message": "Training returned an unreadable response. Please try again."})
                return

    def read_log(self):
        self.log.emit(bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace"))

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.busy = False
            self.event_received.emit({"event": "error", "message": "The training process could not start. Check the application installation."})

    def finished(self, *_):
        self.read_output()
        was_busy = self.busy
        self.busy = False
        self._pending = None
        if self.job_id:
            try:
                cleanup_job(self.job_id, username=self.username)
            except OSError as exc:
                self.log.emit(f"Temporary training data could not be removed: {exc}")
        if was_busy and not self._stopping:
            self.event_received.emit({"event": "error", "message": "Training stopped unexpectedly. Your enabled models are unchanged; try again."})

    def close(self):
        self._stopping = True
        self.busy = False
        self._pending = None
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(3000)
        self._buffer = b""
        self._stopping = False

    def cancel(self):
        self.close()
        self.event_received.emit({"event": "cancelled"})

# -----------------------------------------------------------------------------




# Local account sign-in

class LoginDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Wild-Locate · Welcome')
        self.setWindowIcon(app_icon())
        self.resize(510, 610)
        self.creating = False
        self.username = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(38, 32, 38, 32)
        outer.setSpacing(22)
        brand = QHBoxLayout()
        brand.addWidget(BrandMark())
        brand.addWidget(label('Wild-Locate', 'brand'))
        brand.addStretch()
        outer.addLayout(brand)
        self.title = label('Welcome back.', 'heading')
        outer.addWidget(self.title)
        outer.addWidget(label('Sign in to start exploring wildlife habitat.', 'muted', True))
        card = QFrame()
        card.setObjectName('card')
        body = QVBoxLayout(card)
        body.setContentsMargins(24, 24, 24, 24)
        body.setSpacing(12)
        self.name = QLineEdit()
        self.name.setMaxLength(40)
        self.name.setPlaceholderText('Your username')
        self.password = QLineEdit()
        self.password.setMaxLength(256)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText('Your password')
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm.setMaxLength(256)
        self.confirm.setPlaceholderText('Enter your password again')
        for title, field in [('Username', self.name), ('Password', self.password)]:
            caption = label(title, 'fieldLabel')
            caption.setBuddy(field)
            body.addWidget(caption)
            body.addWidget(field)
        self.confirm.setAccessibleName('Confirm password')
        body.addWidget(self.confirm)
        self.confirm.hide()
        self.error = label('', 'error', True)
        self.error.hide()
        body.addWidget(self.error)
        self.submit = QPushButton('Sign in')
        self.submit.setObjectName('primary')
        self.submit.setDefault(True)
        self.submit.clicked.connect(self.sign_in)
        body.addWidget(self.submit)
        self.switch = QPushButton('New here? Create an account')
        self.switch.setAutoDefault(False)
        self.switch.setObjectName('link')
        self.switch.clicked.connect(self.toggle_mode)
        body.addWidget(self.switch)
        outer.addWidget(card)
        outer.addWidget(label('Accounts stay on this computer. Models you train belong to your account. Bundled models and habitat data are available to everyone.', 'small', True))
        outer.addStretch()

    def toggle_mode(self):
        self.creating = not self.creating
        self.title.setText('Create your account.' if self.creating else 'Welcome back.')
        self.submit.setText('Create account' if self.creating else 'Sign in')
        self.switch.setText('Already have an account? Sign in' if self.creating else 'New here? Create an account')
        self.confirm.setVisible(self.creating)
        self.password.clear()
        self.confirm.clear()
        self.error.hide()

    def sign_in(self):
        try:
            if self.creating and self.password.text() != self.confirm.text():
                raise ValueError('Your passwords don’t match.')
            self.username = authenticate(self.name.text(), self.password.text(), create=self.creating)
        except ValueError as exc:
            self.error.setText(str(exc))
            self.error.show()
            return
        except Exception:
            self.error.setText('We couldn’t open the local account store. Please try again.')
            self.error.show()
            return
        self.password.clear()
        self.confirm.clear()
        self.accept()


# Model management and training

def action(text, callback, primary=False):
    button = QPushButton(text)
    button.setObjectName("primary" if primary else "secondary")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.clicked.connect(callback)
    return button


class SpeciesManager(QDialog):
    models_changed = pyqtSignal()

    def __init__(self, parent=None, region="MA", *, username=None):
        from wildlocate.core.regional import get_region
        self.region = get_region(region)
        self.username = normalize_username(username)
        super().__init__(parent)
        self.setWindowTitle("Manage species · Wild-Locate")
        self.resize(820, 700)
        self.setMinimumSize(660, 560)
        self.client = TrainingClient(self, region=self.region.code, username=self.username)
        self.client.event_received.connect(self.handle_event)
        self.client.log.connect(self.append_log)
        self.taxon = None
        self.prepared = False
        self.state = "idle"
        self.last_action = None
        self.records = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(label("Manage species", "heading"))
        layout.addWidget(label(f"Train and review habitat models for {self.region.name} mammals and reptiles.", "muted", True))
        if self.region.code != "MA":
            layout.addWidget(label("Experimental regional models use national land-cover and terrain data. Missing tiles download during training; this may take a while. Suggested species: " + ", ".join(self.region.examples), "muted", True))
        self.tabs = QTabWidget()
        self.tabs.addTab(self.build_models(), "Your models")
        self.tabs.addTab(self.build_training(), "Train a new species")
        layout.addWidget(self.tabs)
        self.close_button = action("Close", self.reject)
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(self.close_button)
        layout.addLayout(footer)
        self.refresh_models()
        self.update_controls()

    def build_models(self):
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        layout.addWidget(label("Bundled models are ready to use. Models you train are saved to your account and stay here for review until you enable them.", "muted", True))
        self.models = QListWidget()
        self.models.setAccessibleName("Saved species models")
        self.models.setMinimumHeight(150)
        self.models.currentItemChanged.connect(self.show_model)
        layout.addWidget(self.models, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(170)
        self.review = label("Select a model to review its results.", "", True)
        self.review.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.review.setContentsMargins(10, 10, 10, 10)
        scroll.setWidget(self.review)
        layout.addWidget(scroll, 1)
        self.model_message = label("", "notice", True)
        self.model_message.hide()
        layout.addWidget(self.model_message)
        buttons = QHBoxLayout()
        self.enable_button = action("Enable model", self.enable_selected, True)
        self.retrain_button = action("Retrain species", self.retrain_selected)
        self.delete_button = action("Delete custom model", self.delete_selected)
        buttons.addWidget(self.enable_button)
        buttons.addWidget(self.retrain_button)
        buttons.addWidget(self.delete_button)
        layout.addLayout(buttons)
        return page

    def build_training(self):
        page = QWidget()
        page.setObjectName("page")
        outer = QVBoxLayout(page)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("page")
        layout = QVBoxLayout(content)
        layout.setSpacing(12)
        layout.addWidget(label(f"1. Find a {self.region.name} mammal or reptile", "subheading"))
        layout.addWidget(label("Enter a common or scientific name. Observations come from iNaturalist; internet access is needed for downloads.", "muted", True))
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("e.g. American Black Bear")
        self.query.setMaxLength(100)
        self.query.setAccessibleName("Species to train")
        self.query.textChanged.connect(self.query_changed)
        self.query.returnPressed.connect(self.find_species)
        self.find_button = action("Find species", self.find_species)
        row.addWidget(self.query, 1)
        row.addWidget(self.find_button)
        layout.addLayout(row)
        self.match = label("", "notice", True)
        self.match.hide()
        layout.addWidget(self.match)
        self.prepare_button = action("Confirm species and check data", self.prepare_data)
        layout.addWidget(self.prepare_button)
        layout.addWidget(label("2. Prepare and train", "subheading"))
        self.data_summary = label("We check environmental data and count usable observations before training. Training needs at least 25 cleaned observations; spatial validation may require more.", "muted", True)
        layout.addWidget(self.data_summary)
        self.download_button = action("Download environmental data", self.download_environment)
        self.download_button.hide()
        layout.addWidget(self.download_button)
        self.train_button = action("Start training", self.start_training, True)
        layout.addWidget(self.train_button)
        self.status = label("Choose a species to begin.", "", True)
        self.status.setAccessibleName("Training status")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.cancel_button = action("Cancel training", self.client.cancel)
        self.cancel_button.hide()
        layout.addWidget(label("3. Review before enabling", "subheading"))
        layout.addWidget(label("Training compares models automatically using five spatial validation folds. A completed model is saved under Your models for review. Existing models stay available until you enable a replacement.", "muted", True))
        details = Disclosure("Training details")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1500)
        self.log.setMinimumHeight(150)
        self.log.setAccessibleName("Training log")
        details.body_layout.addWidget(self.log)
        layout.addWidget(details)
        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        outer.addWidget(self.status)
        outer.addWidget(self.progress)
        outer.addWidget(self.cancel_button)
        return page

    def append_log(self, text):
        self.log.appendPlainText(text.rstrip())

    def selected_record(self):
        item = self.models.currentItem()
        return self.records.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def refresh_models(self, selected_id=None):
        previous = self.selected_record()
        selected_id = selected_id or (previous.id if previous else None)
        self.records = {r.id: r for r in list_models(self.region.code, username=self.username)}
        active = {r.id for r in available_models(self.region.code, username=self.username).values()}
        self.models.clear()
        for record in self.records.values():
            source = "Custom" if record.custom else "Bundled"
            status = "Enabled" if record.id in active else ("Ready for review" if record.custom else "Available")
            date = f" · {record.created_at[:19].replace('T', ' ')} UTC" if record.created_at else ""
            item = QListWidgetItem(f"{record.species} · {source} · {status}{date}")
            item.setData(Qt.ItemDataRole.UserRole, record.id)
            self.models.addItem(item)
            if record.id == selected_id:
                self.models.setCurrentItem(item)
        if self.models.currentRow() < 0 and self.models.count():
            self.models.setCurrentRow(0)
        self.show_model()

    def show_model(self, *_):
        record = self.selected_record()
        if record is None:
            self.review.setText("No complete models found. Train a species to get started.")
            self.update_controls()
            return
        try:
            metrics = record.metrics()
            selected = next((m for m in metrics.get("metrics_by_model", []) if m["model"] == metrics["selected_model"]), {})
            presence = int(metrics["presence_count"])
            background = int(metrics["background_count"])
            baseline = presence / (presence + background)
            roc = selected.get("mean_roc_auc")
            pr = selected.get("mean_pr_auc")
            text = (f"{record.species}\nModel: {metrics['selected_model']}\n"
                    f"Training locations: {presence:,} observations + {background:,} background samples\n"
                    f"Spatial validation: {metrics['number_of_folds']} folds\n")
            if roc is not None and pr is not None:
                text += f"Mean ROC-AUC: {roc:.3f} · Mean PR-AUC: {pr:.3f}\nPR reference (sample prevalence): {baseline:.3f}\n"
                if roc <= 0.5 or pr <= baseline:
                    text += "Validation did not consistently outperform these simple references. Treat this model as experimental.\n"
            text += ("\nROC-AUC measures separation of observations from background (0.5 is chance). "
                     "PR-AUC summarizes precision and recall and depends on the sampling balance. "
                     "These results do not establish ecological reliability or the probability of an animal being present.")
            self.review.setText(text)
        except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError):
            self.review.setText("Validation details are unavailable. Check this model's saved files.")
        self.update_controls()

    def update_controls(self):
        # Signals during construction can arrive before both tabs exist.
        if not hasattr(self, "train_button"):
            return
        busy = self.client.busy
        record = self.selected_record()
        active = {r.id for r in available_models(self.region.code, username=self.username).values()}
        self.enable_button.setEnabled(not busy and record is not None and record.id not in active)
        self.retrain_button.setEnabled(not busy and record is not None)
        self.delete_button.setEnabled(not busy and record is not None and record.custom)
        self.query.setEnabled(not busy)
        self.find_button.setEnabled(not busy and bool(self.query.text().strip()))
        self.prepare_button.setEnabled(not busy and self.taxon is not None and self.state != "completed")
        self.train_button.setEnabled(not busy and self.prepared)
        self.download_button.setEnabled(not busy)
        self.progress.setVisible(busy)
        self.cancel_button.setVisible(busy)
        self.cancel_button.setText("Cancel training" if self.last_action == "train" else "Cancel operation")

    def query_changed(self):
        self.taxon = None
        self.prepared = False
        self.state = "idle"
        self.match.hide()
        self.download_button.hide()
        self.update_controls()

    def send(self, action_name, **payload):
        if self.client.busy:
            return
        self.last_action = action_name
        self.status.setText("Starting…")
        self.client.request(action_name, **payload)
        self.update_controls()

    def find_species(self):
        if self.client.busy or not self.query.text().strip():
            return
        self.client.close()
        self.taxon = None
        self.prepared = False
        self.state = "idle"
        self.match.hide()
        self.download_button.hide()
        self.log.clear()
        self.send("resolve", query=self.query.text().strip())

    def prepare_data(self):
        if self.taxon is not None:
            self.prepared = False
            self.send("prepare")

    def start_training(self):
        if self.prepared:
            self.prepared = False
            self.send("train")

    def download_environment(self):
        self.send("initialize")

    def handle_event(self, event):
        kind = event["event"]
        if kind == "progress":
            self.status.setText(event["message"])
            return
        if kind == "resolved":
            self.taxon = event
            self.state = "resolved"
            group_name = "Reptile" if event.get("iconic_taxon_name") == "Reptilia" else "Mammal"
            self.match.setText(
                f"{event['common_name']} ({event['scientific_name']})\n"
                f"{group_name} species · {self.region.name} observations only"
            )
            self.match.show()
            self.status.setText("Confirm this species to download and check its observations.")
        elif kind == "prepared":
            self.prepared = True
            self.state = "prepared"
            self.download_button.hide()
            self.data_summary.setText(f"{event['cleaned_count']:,} usable observations from {event['raw_count']:,} downloaded records. Environmental datasets are available. Spatial coverage will be checked during training.")
            self.status.setText("Ready to train. This may take several minutes or longer; you can cancel at any time.")
        elif kind == "initialized":
            self.download_button.hide()
            self.status.setText("Environmental datasets downloaded. Confirm the species and check data again.")
        elif kind == "completed":
            self.state = "completed"
            self.prepared = False
            self.status.setText("Training complete. Review the saved model before enabling it.")
            self.refresh_models(event["model_id"])
            self.tabs.setCurrentIndex(0)
            self.model_message.setText("Training complete. Review validation results below the model list, then choose Enable model when ready.")
            self.model_message.show()
        elif kind == "cancelled":
            self.taxon = None
            self.prepared = False
            self.match.hide()
            self.state = "idle"
            self.status.setText("Training cancelled. Your enabled models are unchanged. Find a species to try again.")
            self.refresh_models()
        elif kind == "error":
            self.prepared = False
            self.status.setText(event["message"])
            self.append_log(event["message"])
            self.download_button.setVisible(event.get("code") == "missing_environment")
        self.update_controls()

    def enable_selected(self):
        record = self.selected_record()
        if record is None:
            return
        try:
            enable_model(record.id, username=self.username)
            self.models_changed.emit()
            self.refresh_models(record.id)
            self.model_message.setText(f"{record.species} is now available in the analysis dropdown.")
            self.model_message.show()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Could not enable model", str(exc))

    def retrain_selected(self):
        record = self.selected_record()
        if record:
            self.tabs.setCurrentIndex(1)
            self.query.setText(record.species)
            self.find_species()

    def delete_selected(self):
        record = self.selected_record()
        if record is None or not record.custom:
            return
        answer = QMessageBox.question(self, "Delete custom model?",
                                      f"Delete this saved model for {record.species}? Bundled models are kept. This cannot be undone.",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_model(record.id, username=self.username)
            self.models_changed.emit()
            self.refresh_models()
            self.model_message.hide()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Could not delete model", str(exc))

    def reject(self):
        if self.client.busy:
            answer = QMessageBox.question(self, "Cancel training and close?", "The current operation will stop. Completed models are kept.",
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                          QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.client.close()
        super().reject()

    def closeEvent(self, event):
        event.ignore()
        self.reject()

# -----------------------------------------------------------------------------





def button(text, role="", callback=None):
    widget = QPushButton(text)
    widget.setObjectName(role)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if callback:
        widget.clicked.connect(callback)
    return widget


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
        lat, lon = (42.28, -71.35) if self.species.currentText() == "Red Fox" else (42.3718, -72.2820)
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
