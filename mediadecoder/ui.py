"""PySide6 user interface: one main window, a drop zone, a results panel, a settings dialog."""
from __future__ import annotations

import json
import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
                               QSizePolicy, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from . import ai, export, pipeline
from .config import APP_NAME, APP_VERSION, EFFORTS, MODELS, WHISPER_MODELS, Settings
from .media import SUPPORTED, Cancelled, MediaError, detect_kind

STYLE = """
QMainWindow, QDialog { background: #F6F7F9; }
QWidget { color: #1C2430; font-size: 10pt; }
#Title { font-size: 15pt; font-weight: 600; }
#Subtle { color: #5B6675; }
#DropZone { background: #FFFFFF; border: 2px dashed #A9B4C2; border-radius: 12px; }
#DropZone[hover="true"] { border-color: #2F6FED; background: #EEF4FF; }
#Card { background: #FFFFFF; border: 1px solid #DCE1E8; border-radius: 10px; }
#Summary { background: #FFFFFF; border: 1px solid #DCE1E8; border-radius: 10px; }
#StepTitle { font-size: 11.5pt; font-weight: 600; }
#Badge { background: #FFF4D6; color: #7A5200; border-radius: 8px; padding: 2px 8px; font-size: 8.5pt; font-weight: 600; }
#BadgeFact { background: #DDF5E6; color: #145A32; border-radius: 8px; padding: 2px 8px; font-size: 8.5pt; font-weight: 600; }
#FieldLabel { color: #5B6675; font-weight: 600; font-size: 9pt; }
#Error { background: #FDECEC; color: #8A1C1C; border: 1px solid #F3B9B9; border-radius: 8px; padding: 10px; }
#Warn { background: #FFF8E5; color: #6B4E00; border: 1px solid #F1D99A; border-radius: 8px; padding: 8px; }
QPlainTextEdit#Code { background: #0F1720; color: #E6EDF3; border-radius: 6px; padding: 6px; }
QPushButton { background: #FFFFFF; border: 1px solid #C5CDD8; border-radius: 6px; padding: 5px 12px; }
QPushButton:hover { border-color: #2F6FED; }
QPushButton:disabled { color: #9AA4B2; }
QPushButton#Primary { background: #2F6FED; color: white; border: none; }
QPushButton#Primary:hover { background: #2559C4; }
QProgressBar { border: 1px solid #C5CDD8; border-radius: 6px; background: #FFFFFF; height: 14px; text-align: center; }
QProgressBar::chunk { background: #2F6FED; border-radius: 5px; }
"""


def copy_text(text: str) -> None:
    QGuiApplication.clipboard().setText(text)


def mono_font() -> QFont:
    font = QFont("Consolas", 10)
    font.setStyleHint(QFont.Monospace)
    if not QFontDatabase.hasFamily("Consolas"):
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    return font


class Worker(QObject):
    progress = Signal(float, str)
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, path: str, settings: Settings):
        super().__init__()
        self.path, self.settings = path, settings
        self.cancel_event = threading.Event()

    @Slot()
    def run(self):
        try:
            result = pipeline.run(Path(self.path), self.settings,
                                  lambda p, m: self.progress.emit(float(p), m), self.cancel_event.is_set)
            self.done.emit(result)
        except Cancelled:
            self.failed.emit("__cancelled__")
        except MediaError as exc:
            self.failed.emit(str(exc))
        except MemoryError:
            self.failed.emit("Ran out of memory while analyzing. Lower 'Max duration' in Settings.")
        except Exception as exc:  # unexpected: show the type and log the traceback
            traceback.print_exc()
            self.failed.emit(f"Unexpected error: {type(exc).__name__}: {exc}")


class DropZone(QFrame):
    fileChosen = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(130)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        self.main = QLabel("Drop an image, video, GIF or audio file here")
        self.main.setStyleSheet("font-size: 12.5pt; font-weight: 600;")
        self.main.setAlignment(Qt.AlignCenter)
        exts = " ".join(sorted(e.lstrip(".").upper() for e in SUPPORTED))
        sub = QLabel(f"or click to browse  ·  {exts}")
        sub.setObjectName("Subtle")
        sub.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.main)
        lay.addWidget(sub)

    def _set_hover(self, on: bool):
        self.setProperty("hover", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.isEnabled():
            self.browse()

    def browse(self):
        pattern = " ".join(f"*{e}" for e in SUPPORTED)
        path, _ = QFileDialog.getOpenFileName(self, "Choose media file", "", f"Media files ({pattern});;All files (*)")
        if path:
            self.fileChosen.emit(path)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()
            self._set_hover(True)

    def dragLeaveEvent(self, event):
        self._set_hover(False)

    def dropEvent(self, event):
        self._set_hover(False)
        files = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if files:
            self.fileChosen.emit(files[0])


class CodeBox(QWidget):
    def __init__(self, label: str, text: str):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        row = QHBoxLayout()
        lab = QLabel(label)
        lab.setObjectName("FieldLabel")
        btn = QPushButton("Copy")
        btn.setMinimumWidth(78)
        btn.clicked.connect(lambda: (copy_text(text), btn.setText("Copied")))
        row.addWidget(lab)
        row.addStretch()
        row.addWidget(btn)
        lay.addLayout(row)
        box = QPlainTextEdit(text)
        box.setObjectName("Code")
        box.setReadOnly(True)
        box.setFont(mono_font())
        box.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        lines = max(1, sum(1 + len(line) // 85 for line in text.splitlines()))
        box.setFixedHeight(min(280, 30 + lines * box.fontMetrics().lineSpacing()))
        lay.addWidget(box)


class StepCard(QFrame):
    def __init__(self, step: dict):
        super().__init__()
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        head = QHBoxLayout()
        title = QLabel(f"Step {step['number']} · {step['title']}")
        title.setObjectName("StepTitle")
        title.setWordWrap(True)
        badge = QLabel("ESTIMATED" if step.get("estimated", True) else "FROM FILE METADATA")
        badge.setObjectName("Badge" if step.get("estimated", True) else "BadgeFact")
        copy = QPushButton("Copy step")
        copy.clicked.connect(lambda: (copy_text(export.step_to_text(step)), copy.setText("Copied")))
        head.addWidget(title, 1)
        head.addWidget(badge)
        head.addWidget(copy)
        lay.addLayout(head)
        meta = []
        if step.get("time_range"):
            meta.append(f"<b>Time:</b> {_esc(step['time_range'])}")
        meta.append(f"<b>Tool:</b> {_esc(step.get('tool', ''))}")
        info = QLabel("  ·  ".join(meta) + f"<br><b>Produces:</b> {_esc(step.get('produces', ''))}")
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(info)
        for key, label in (("prompt", "PROMPT"), ("negative_prompt", "NEGATIVE PROMPT"),
                           ("command", "COMMAND"), ("settings", "SETTINGS / ACTIONS")):
            if step.get(key):
                lay.addWidget(CodeBox(label, step[key]))
        if step.get("evidence"):
            ev = QLabel(f"Evidence: {step['evidence']}")
            ev.setObjectName("Subtle")
            ev.setWordWrap(True)
            lay.addWidget(ev)


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class SettingsDialog(QDialog):
    def __init__(self, parent, settings: Settings):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(620)
        self.settings = settings
        form = QFormLayout()
        self.claude_status = QLabel()
        self.claude_status.setWordWrap(True)
        self.claude_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        sign_in = QPushButton("Sign in")
        sign_in.clicked.connect(self.sign_in)
        recheck = QPushButton("Check again")
        recheck.clicked.connect(self.refresh_status)
        status_row = QHBoxLayout()
        status_row.addWidget(self.claude_status, 1)
        status_row.addWidget(sign_in)
        status_row.addWidget(recheck)
        form.addRow("Claude account", status_row)
        self.claude_path = QLineEdit(settings.claude_path)
        self.claude_path.setPlaceholderText("auto-detect claude.exe")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse_claude)
        path_row = QHBoxLayout()
        path_row.addWidget(self.claude_path, 1)
        path_row.addWidget(browse)
        form.addRow("Claude CLI path", path_row)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItems(MODELS)
        self.model.setCurrentText(settings.model)
        form.addRow("Model", self.model)
        self.effort = QComboBox()
        self.effort.addItems(EFFORTS)
        self.effort.setCurrentText(settings.effort)
        form.addRow("Effort", self.effort)
        self.interval = QDoubleSpinBox(minimum=1, maximum=60, value=settings.frame_interval_s, suffix=" s")
        form.addRow("Extra frame every", self.interval)
        self.max_frames = QSpinBox(minimum=4, maximum=90, value=settings.max_frames)
        form.addRow("Max frames sent", self.max_frames)
        self.threshold = QDoubleSpinBox(minimum=0.05, maximum=0.9, singleStep=0.05, value=settings.scene_threshold)
        form.addRow("Cut sensitivity (lower = more cuts)", self.threshold)
        self.max_dur = QDoubleSpinBox(minimum=0.5, maximum=120, value=settings.max_duration_min, suffix=" min")
        form.addRow("Max duration analyzed", self.max_dur)
        self.max_mb = QSpinBox(minimum=10, maximum=20000, value=settings.max_file_mb, suffix=" MB")
        form.addRow("Max file size", self.max_mb)
        self.transcribe = QCheckBox("Transcribe speech locally (Whisper)")
        self.transcribe.setChecked(settings.transcribe)
        form.addRow("", self.transcribe)
        self.whisper = QComboBox()
        self.whisper.addItems(WHISPER_MODELS)
        self.whisper.setCurrentText(settings.whisper_model)
        form.addRow("Whisper model", self.whisper)
        self.timeout = QDoubleSpinBox(minimum=2, maximum=120, value=settings.timeout_min, suffix=" min")
        form.addRow("Claude time limit", self.timeout)
        note = QLabel("No API key or credits: the AI step runs through Claude Code signed in with your Claude "
                      "Pro/Max subscription and counts toward that plan's usage limits.")
        note.setObjectName("Subtle")
        note.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(note)
        lay.addWidget(buttons)
        self.refresh_status()

    def _exe(self):
        return ai.find_claude(self.claude_path.text().strip())

    def refresh_status(self):
        exe = self._exe()
        if not exe:
            self.claude_status.setText(f"<b>Claude Code not found.</b> {ai.INSTALL_HINT}")
            return
        try:
            st = ai.auth_status(exe)
        except MediaError as exc:
            self.claude_status.setText(str(exc))
            return
        if st.get("loggedIn"):
            account = st.get("account") if isinstance(st.get("account"), dict) else {}
            who = st.get("email") or account.get("email")
            plan = st.get("subscriptionType") or st.get("plan") or ""
            text = "✔ Signed in" + (f" as {who}" if who else "") + (f" ({plan})" if plan else "")
            if st.get("authMethod") in ("api_key", "apiKey"):
                text = "⚠ Signed in with an API key (paid credits). Log out and sign in with your subscription."
            self.claude_status.setText(text)
        else:
            self.claude_status.setText("✖ Not signed in. Click <b>Sign in</b>, finish in the browser, then <b>Check again</b>.")

    def sign_in(self):
        exe = self._exe()
        if not exe:
            self.claude_status.setText(f"<b>Claude Code not found.</b> {ai.INSTALL_HINT}")
            return
        ai.open_sign_in(exe)
        self.claude_status.setText("A sign-in window opened. Finish signing in, then click <b>Check again</b>.")

    def browse_claude(self):
        path, _ = QFileDialog.getOpenFileName(self, "Locate claude.exe", "", "claude.exe (claude.exe);;All files (*)")
        if path:
            self.claude_path.setText(path)
            self.refresh_status()

    def save(self):
        s = self.settings
        s.model = self.model.currentText().strip() or MODELS[0]
        s.effort = self.effort.currentText()
        s.frame_interval_s = self.interval.value()
        s.max_frames = self.max_frames.value()
        s.scene_threshold = self.threshold.value()
        s.max_duration_min = self.max_dur.value()
        s.max_file_mb = self.max_mb.value()
        s.transcribe = self.transcribe.isChecked()
        s.whisper_model = self.whisper.currentText()
        s.claude_path = self.claude_path.text().strip()
        s.timeout_min = self.timeout.value()
        s.save()
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, initial_file: str | None = None):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — media to step-by-step recipe")
        self.resize(1040, 820)
        self.settings = Settings.load()
        self.result: dict | None = None
        self.thread: QThread | None = None
        self.worker: Worker | None = None

        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 14, 18, 14)
        top = QHBoxLayout()
        title = QLabel(APP_NAME)
        title.setObjectName("Title")
        sub = QLabel("Drop finished media, get the estimated recipe behind it")
        sub.setObjectName("Subtle")
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.clicked.connect(self.open_settings)
        top.addWidget(title)
        top.addSpacing(10)
        top.addWidget(sub)
        top.addStretch()
        top.addWidget(self.settings_btn)
        outer.addLayout(top)

        self.drop = DropZone()
        self.drop.fileChosen.connect(self.start)
        outer.addWidget(self.drop)

        prog = QHBoxLayout()
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.status = QLabel("Ready.")
        self.status.setObjectName("Subtle")
        self.status.setMinimumWidth(380)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel)
        self.cancel_btn.setEnabled(False)
        prog.addWidget(self.bar, 1)
        prog.addWidget(self.status, 1)
        prog.addWidget(self.cancel_btn)
        outer.addLayout(prog)

        self.error = QLabel()
        self.error.setObjectName("Error")
        self.error.setWordWrap(True)
        self.error.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.error.hide()
        outer.addWidget(self.error)

        self.tabs = QTabWidget()
        self.recipe_scroll = QScrollArea()
        self.recipe_scroll.setWidgetResizable(True)
        self.recipe_scroll.setFrameShape(QFrame.NoFrame)
        self._show_placeholder()
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setFont(mono_font())
        self.tabs.addTab(self.recipe_scroll, "Recipe")
        self.tabs.addTab(self.raw, "Extracted data")
        outer.addWidget(self.tabs, 1)

        bottom = QHBoxLayout()
        self.copy_all = QPushButton("Copy all steps")
        self.copy_all.setObjectName("Primary")
        self.copy_all.clicked.connect(lambda: self._copy_all())
        bottom.addWidget(self.copy_all)
        bottom.addStretch()
        self.export_btns = []
        for label, ext in (("Export .txt", "txt"), ("Export .md", "md"), ("Export .json", "json")):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, e=ext: self.export(e))
            bottom.addWidget(b)
            self.export_btns.append(b)
        outer.addLayout(bottom)
        self._set_result_buttons(False)
        self.setCentralWidget(root)
        if initial_file:
            self.start(initial_file)

    # ---- helpers -------------------------------------------------------------------------
    def _show_placeholder(self):
        w = QLabel("Results appear here as numbered steps you can copy or export.")
        w.setObjectName("Subtle")
        w.setAlignment(Qt.AlignCenter)
        self.recipe_scroll.setWidget(w)

    def _set_result_buttons(self, on: bool):
        self.copy_all.setEnabled(on)
        for b in self.export_btns:
            b.setEnabled(on)

    def _busy(self, on: bool):
        self.drop.setEnabled(not on)
        self.settings_btn.setEnabled(not on)
        self.cancel_btn.setEnabled(on)

    def show_error(self, msg: str):
        self.error.setText(f"⚠  {msg}")
        self.error.show()

    def _copy_all(self):
        if self.result:
            copy_text(export.all_steps_text(self.result))
            self.statusBar().showMessage("All steps copied to the clipboard.", 3000)

    # ---- actions -------------------------------------------------------------------------
    def open_settings(self):
        SettingsDialog(self, self.settings).exec()

    def start(self, path: str):
        self.error.hide()
        try:
            detect_kind(Path(path))
        except MediaError as exc:
            self.show_error(str(exc))
            return
        self.result = None
        self._set_result_buttons(False)
        self._show_placeholder()
        self.raw.clear()
        self.drop.main.setText(Path(path).name)
        self._busy(True)
        self.bar.setValue(0)
        self.thread = QThread(self)
        self.worker = Worker(path, self.settings)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.done.connect(self.on_done)
        self.worker.failed.connect(self.on_failed)
        self.worker.done.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.start()

    def cancel(self):
        if self.worker:
            self.worker.cancel_event.set()
            self.status.setText("Cancelling…")

    @Slot(float, str)
    def on_progress(self, pct: float, msg: str):
        self.bar.setValue(int(pct * 10))
        self.status.setText(msg)

    @Slot(str)
    def on_failed(self, msg: str):
        self._busy(False)
        self.bar.setValue(0)
        self.drop.main.setText("Drop an image, video, GIF or audio file here")
        if msg == "__cancelled__":
            self.status.setText("Cancelled.")
            return
        self.status.setText("Failed.")
        self.show_error(msg)

    @Slot(dict)
    def on_done(self, result: dict):
        self._busy(False)
        self.result = result
        self.bar.setValue(1000)
        meta = result.get("meta", {})
        self.status.setText(f"Done in {meta.get('elapsed_s', '?')} s · {meta.get('model', '')} · "
                            f"{meta.get('input_tokens', 0):,} in / {meta.get('output_tokens', 0):,} out tokens")
        self.raw.setPlainText(json.dumps(result["analysis"], indent=2, ensure_ascii=False, default=str))
        self.render_recipe(result)
        self._set_result_buttons(True)

    def render_recipe(self, result: dict):
        r = result["recipe"]
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(2, 8, 8, 8)
        lay.setSpacing(10)
        summary = QFrame()
        summary.setObjectName("Summary")
        sl = QVBoxLayout(summary)
        sl.setContentsMargins(14, 12, 14, 12)
        t = QLabel(r["title"])
        t.setObjectName("StepTitle")
        t.setWordWrap(True)
        body = QLabel(f"{_esc(r['summary'])}<br><br><b>Likely origin:</b> {_esc(r['likely_origin'])} "
                      f"(confidence: {_esc(r['confidence'])})<br><b>Style:</b> {_esc(', '.join(r.get('style_keywords', [])))}"
                      f"<br><b>File:</b> {_esc(result['file']['name'])} · {len(r['steps'])} steps")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        sl.addWidget(t)
        sl.addWidget(body)
        lay.addWidget(summary)
        disclaimer = QLabel("All steps are ESTIMATED: they reconstruct a plausible process from the finished file; "
                            "the real one cannot be known.")
        disclaimer.setObjectName("Warn")
        disclaimer.setWordWrap(True)
        lay.addWidget(disclaimer)
        warnings = result["analysis"].get("warnings") or []
        if warnings:
            w = QLabel("Notes: " + " ".join(warnings))
            w.setObjectName("Warn")
            w.setWordWrap(True)
            lay.addWidget(w)
        for step in r["steps"]:
            lay.addWidget(StepCard(step))
        lay.addStretch()
        page.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.recipe_scroll.setWidget(page)
        self.tabs.setCurrentIndex(0)

    def export(self, ext: str):
        if not self.result:
            return
        stem = Path(self.result["file"]["name"]).stem + "_recipe"
        filters = {"txt": "Text (*.txt)", "md": "Markdown (*.md)", "json": "JSON (*.json)"}
        path, _ = QFileDialog.getSaveFileName(self, "Export recipe", f"{stem}.{ext}", filters[ext])
        if not path:
            return
        text = {"txt": export.to_text, "md": export.to_markdown, "json": export.to_json}[ext](self.result)
        try:
            Path(path).write_text(text, encoding="utf-8")
            self.statusBar().showMessage(f"Saved {path}", 5000)
        except OSError as exc:
            self.show_error(f"Could not save the file: {exc}")

    def closeEvent(self, event):
        if self.worker:
            self.worker.cancel_event.set()
        if self.thread and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait(3000)
        super().closeEvent(event)


def run_gui(initial_file: str | None = None) -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)  # the stylesheet is a light theme
    except AttributeError:
        pass
    app.setStyle("Fusion")
    app.setPalette(app.style().standardPalette())
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(STYLE)
    win = MainWindow(initial_file)
    win.show()
    return app.exec()
