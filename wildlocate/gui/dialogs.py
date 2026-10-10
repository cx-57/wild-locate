"""Account and species management dialogs"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget
from wildlocate.core.registry import authenticate, available_models, delete_model, enable_model, list_models, normalize_username
from wildlocate.gui.ui import label, app_icon, BrandMark, Disclosure
from wildlocate.gui.jobs import TrainingClient

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

def button(text, role="", callback=None):
    widget = QPushButton(text)
    widget.setObjectName(role)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if callback:
        widget.clicked.connect(callback)
    return widget
