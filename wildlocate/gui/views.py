"""Desktop result layout and display methods"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QBoxLayout, QFrame, QGridLayout, QHBoxLayout,
    QProgressBar, QSizePolicy, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from wildlocate.core.regional import REGIONS
from wildlocate.gui.ui import (
    BrandMark, ChoiceBox, Disclosure, InsightsPanel, SuitabilityGauge,
    coordinates, divider, feature_display, label, ordinal,
)
from wildlocate.gui.dialogs import button


class ResultViewMixin:
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
