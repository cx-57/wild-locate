"""Reusable desktop widgets and styling"""

import math
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PyQt6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QStyledItemDelegate, QTabWidget, QVBoxLayout, QWidget

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
        # Draw the chevron because the custom combo style hides Qt's arrow
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
