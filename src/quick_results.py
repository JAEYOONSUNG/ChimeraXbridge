"""Compact results, candidate comparison and undo for quick toolbar actions."""
from html import escape
import math
import weakref

from Qt.QtCore import Qt
from Qt.QtWidgets import (QTreeWidget, QTreeWidgetItem, QHeaderView,
                          QAbstractItemView)

from chimerax.core.tools import ToolInstance, get_singleton
from .panel_scroll import PanelScrollArea


_INDEX_ROLE = int(Qt.ItemDataRole.UserRole)
_SORT_ROLE = _INDEX_ROLE + 1
_SEARCH_ROLE = _INDEX_ROLE + 2


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _short_candidate(candidate):
    kind = candidate.get("kind") or candidate.get("source")
    if kind == "geometry":
        return f"{candidate.get('model_spec', '')} · {str(candidate.get('id', '')).rsplit(':', 1)[-1]}"
    if kind == "selection":
        return "Selection"
    anchors = candidate.get("anchors", ())
    if anchors:
        return str(anchors[0].get("label", candidate.get("label", "Candidate")))
    return " · ".join(str(candidate.get("label", "Candidate")).split(" · ")[:2])


class _CandidateItem(QTreeWidgetItem):
    def __lt__(self, other):
        tree = self.treeWidget()
        column = tree.sortColumn()
        ascending = tree.header().sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
        left, right = self.data(column, _SORT_ROLE), other.data(column, _SORT_ROLE)
        if left is None or right is None:
            if left is None and right is None:
                return False
            return (right is None) if ascending else (left is None)
        if left == right:
            a, b = self.data(0, _INDEX_ROLE), other.data(0, _INDEX_ROLE)
            return a < b if ascending else a > b
        return left < right


class CandidateTable(QTreeWidget):
    """Comparison whose source indices survive numeric sorting and filtering."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items_by_index = {}
        self.setColumnCount(5)
        self.setHeaderLabels(["#", "Candidate", "Contacts", "Vol Å³", "Depth Å"])
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setAllColumnsShowFocus(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        header = self.header()
        header.setMinimumSectionSize(20)
        header.setStretchLastSection(False)
        for column, width in ((0, 26), (2, 56), (3, 56), (4, 60)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.setColumnWidth(column, width)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.headerItem().setToolTip(0, "Original automatic rank; selected regions and evidence may precede volume.")
        self.headerItem().setToolTip(2, "Measured contact/lining residue count. — means unavailable.")
        self.headerItem().setToolTip(3, "Cavity volume in Å³; exports retain full precision.")
        self.headerItem().setToolTip(4, "Maximum depth in Å. — means unavailable.")
        self.setSortingEnabled(True)
        self.sortItems(0, Qt.SortOrder.AscendingOrder)

    def clear(self):
        super().clear()
        self._items_by_index = {}

    def set_candidates(self, candidates):
        blocked = self.blockSignals(True)
        sort_column = self.sortColumn()
        sort_order = self.header().sortIndicatorOrder()
        self.setSortingEnabled(False)
        self.clear()
        availability = [False, False, False]
        for index, candidate in enumerate(candidates):
            values = [_number(candidate.get("contact_residues", candidate.get("contact_count"))),
                      _number(candidate.get("volume")), _number(candidate.get("max_depth"))]
            labels = ["—" if value is None else format(value, ".1f" if pos == 2 else ".0f")
                      for pos, value in enumerate(values)]
            item = _CandidateItem([str(index + 1), _short_candidate(candidate), *labels])
            item.setData(0, _INDEX_ROLE, index)
            item.setData(0, _SORT_ROLE, index + 1)
            item.setData(1, _SORT_ROLE, item.text(1).casefold())
            for column, value in enumerate(values, 2):
                item.setData(column, _SORT_ROLE, value)
                item.setTextAlignment(column, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                availability[column - 2] |= value is not None
            full_text = "\n".join([str(candidate.get("label", "Candidate")),
                *map(str, candidate.get("specs", ())), *map(str, candidate.get("evidence", ()))])
            item.setData(0, _SEARCH_ROLE, full_text.casefold())
            for column in range(5):
                item.setToolTip(column, full_text)
            self.addTopLevelItem(item)
            self._items_by_index[index] = item
        for column, available in enumerate(availability, 2):
            self.setColumnHidden(column, not available)
        self.setSortingEnabled(True)
        self.sortItems(sort_column, sort_order)
        self.blockSignals(blocked)

    def filter_candidates(self, query):
        tokens = str(query).casefold().split()
        current = self.currentItem()
        blocked = self.blockSignals(True)
        for item in self._items_by_index.values():
            item.setHidden(not all(token in item.data(0, _SEARCH_ROLE) for token in tokens))
        if self.currentItem() is not current:
            self.setCurrentItem(current)
        self.blockSignals(blocked)
        return len(self.visible_items())

    def visible_items(self):
        return [self.topLevelItem(i) for i in range(self.topLevelItemCount())
                if not self.topLevelItem(i).isHidden()]

    def adjacent_index(self, direction):
        visible = self.visible_items()
        if not visible:
            return None
        current = self.currentItem()
        if current not in visible:
            return self.row(visible[0 if direction > 0 else -1])
        return self.row(visible[(visible.index(current) + direction) % len(visible)])

    def item(self, index):
        return self._items_by_index.get(index)

    def row(self, item):
        return int(item.data(0, _INDEX_ROLE)) if item is not None else -1

    def count(self):
        return len(self._items_by_index)

    def currentRow(self):
        return self.row(self.currentItem())

    def setCurrentRow(self, index):
        blocked = self.blockSignals(True)
        item = self.item(index)
        if item is None:
            self.clearSelection()
            self.setCurrentItem(None)
        else:
            self.setCurrentItem(item)
            self.scrollToItem(item)
        self.blockSignals(blocked)


class _QuickScrollArea(PanelScrollArea):
    """Compatibility name for the shared full-panel scroll behavior."""


class QuickResults(ToolInstance):
    SESSION_SAVE = False
    SESSION_ENDURING = False

    @classmethod
    def get_singleton(cls, session, create=True, display=True):
        for tool in session.tools.find_by_class(cls):
            if tool.display_name == "Quick Results" and getattr(tool, "_disposed", False):
                tool.delete()
        return get_singleton(session, cls, "Quick Results", create=create, display=display)

    def __init__(self, session, name):
        super().__init__(session, name)
        from Qt.QtWidgets import (QVBoxLayout, QHBoxLayout, QWidget, QLabel,
                                  QPushButton, QProgressBar, QTextBrowser, QToolButton,
                                  QMenu, QLineEdit, QCheckBox)
        from Qt.QtCore import Qt
        from pathlib import Path
        from chimerax.ui import MainToolWindow
        from .camera_bookmarks import _BookmarkStatus, _BookmarkCombo
        from .ui_theme import panel_stylesheet
        self.controller = None
        self.job = None
        self._disposed = False
        self._native_destroyed = False
        self._delete_called = False
        self._comparison_loading = False
        self._report_text = ""
        self.tool_window = MainToolWindow(self, close_destroys=True)
        parent = self.tool_window.ui_area
        parent.destroyed.connect(lambda: self._dispose(native_destroyed=True))
        parent.setObjectName("codex_quick_results")
        chevron = (Path(__file__).parent / "icons/chevron-down.svg").as_posix()
        parent.setStyleSheet(panel_stylesheet("codex_quick_results") +
            "QWidget { font-size: 12px; }"
            "QPushButton, QToolButton, QComboBox, QLineEdit { min-height: 20px; max-height: 20px; }"
            "QToolButton#quick_report { padding-right: 18px; }"
            "QToolButton#quick_report::menu-indicator { width: 8px; height: 8px;"
            " subcontrol-position: right center; right: 4px; }"
            "QComboBox { padding-right: 22px; }"
            f"QComboBox::down-arrow {{ image: url('{chevron}'); width: 12px; height: 12px; }}"
            "QLabel#quick_title { font-size: 15px; font-weight: 600; }"
            "QListWidget::item { padding: 4px; min-height: 24px; }"
            "QProgressBar { min-height: 5px; max-height: 5px; border: none; }")
        outer = QVBoxLayout(parent)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = _QuickScrollArea(parent)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content = QWidget()
        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(5)
        self.title = QLabel("Quick actions")
        self.title.setObjectName("quick_title")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.title)
        self.target = _BookmarkCombo()
        self.target.setMinimumContentsLength(12)
        self.target.setSizeAdjustPolicy(self.target.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.target.currentIndexChanged.connect(self._target_changed)
        self.target.setToolTip("Run the current action for another open structure.")
        layout.addWidget(self.target)
        self.status = _BookmarkStatus("Choose Analyze, View, Pocket, Cavity, Figure or Zoom.")
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)
        buttons = QHBoxLayout()
        self.rerun_button = QToolButton()
        self.rerun_button.setText("Run again")
        self.rerun_button.setEnabled(False)
        self.rerun_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.rerun_button.setToolTip("Run for this target using valid cached results. The arrow offers a fresh calculation.")
        rerun_menu = QMenu(self.rerun_button)
        self.recalculate_action = rerun_menu.addAction("Recalculate without cache")
        self.recalculate_action.triggered.connect(lambda: self._rerun(force=True))
        self.rerun_button.setMenu(rerun_menu)
        self.rerun_button.clicked.connect(self._rerun)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(lambda: self.controller and self.controller.cancel_active())
        self.cancel_button.setEnabled(False)
        self.undo_button = QPushButton("Undo view")
        self.undo_button.clicked.connect(lambda: self.controller and self.controller.undo())
        self.undo_button.setEnabled(False)
        for button in (self.rerun_button, self.cancel_button, self.undo_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.summary = QTextBrowser()
        self.summary.setOpenExternalLinks(False)
        self.summary.setMinimumHeight(95)
        self.summary.setMinimumWidth(0)
        layout.addWidget(self.summary, 1)
        candidate_header = QHBoxLayout()
        self.candidate_label = _BookmarkStatus("Candidates · compare")
        candidate_header.addWidget(self.candidate_label, 1)
        self.overlay_check = QCheckBox("Overlay")
        self.overlay_check.setChecked(True)
        self.overlay_check.setToolTip("Show or hide generated cavity surfaces and analysis labels; original models stay unchanged.")
        self.overlay_check.toggled.connect(self._overlay_changed)
        candidate_header.addWidget(self.overlay_check)
        layout.addLayout(candidate_header)
        self.candidate_controls = QWidget()
        navigation = QHBoxLayout(self.candidate_controls)
        navigation.setContentsMargins(0, 0, 0, 0)
        navigation.setSpacing(4)
        self.candidate_filter = QLineEdit()
        self.candidate_filter.setPlaceholderText("Filter name, residue or evidence…")
        self.candidate_filter.setClearButtonEnabled(True)
        self.candidate_filter.setMinimumWidth(0)
        self.candidate_filter.setToolTip("Filter this table without changing the current preview. Clear the filter to show all candidates; reports always include every candidate.")
        self.candidate_filter.textChanged.connect(self._filter_candidates)
        navigation.addWidget(self.candidate_filter, 1)
        self.previous_button = QToolButton()
        self.previous_button.setText("◀")
        self.previous_button.setFixedWidth(28)
        self.previous_button.setToolTip("Previous visible candidate in the current sort order")
        self.previous_button.clicked.connect(lambda: self._step_candidate(-1))
        self.next_button = QToolButton()
        self.next_button.setText("▶")
        self.next_button.setFixedWidth(28)
        self.next_button.setToolTip("Next visible candidate in the current sort order")
        self.next_button.clicked.connect(lambda: self._step_candidate(1))
        navigation.addWidget(self.previous_button)
        navigation.addWidget(self.next_button)
        layout.addWidget(self.candidate_controls)
        self.candidates = CandidateTable()
        self.candidates.setMinimumHeight(80)
        self.candidates.setMaximumHeight(180)
        self.candidates.currentItemChanged.connect(self._candidate_changed)
        self.candidates.itemActivated.connect(lambda item, _column: self._candidate_changed(item, None))
        self.candidates.header().sortIndicatorChanged.connect(self._remember_comparison)
        layout.addWidget(self.candidates, 2)
        self.details_toggle = QPushButton("Evidence and limits ▸")
        self.details_toggle.setCheckable(True)
        self.details_toggle.toggled.connect(self._details_changed)
        layout.addWidget(self.details_toggle)
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(False)
        self.details.setMinimumHeight(90)
        self.details.setMaximumHeight(190)
        self.details.hide()
        layout.addWidget(self.details)
        footer = QHBoxLayout()
        self.copy_button = QPushButton("Copy report")
        self.copy_button.setEnabled(False)
        self.copy_button.setToolTip("Copy the complete report with all candidates, including rows hidden by the filter. The current preview is identified in the report.")
        self.copy_button.clicked.connect(self._copy_report)
        self.report_button = QToolButton()
        self.report_button.setObjectName("quick_report")
        self.report_button.setText("Save report")
        self.report_button.setToolTip("Save all candidates with their original IDs and measurements. Table filters and sorting do not limit or reorder exported results.")
        self.report_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        report_menu = QMenu(self.report_button)
        for name, fmt in (("CSV table…", "csv"), ("JSON data…", "json"), ("Markdown report…", "markdown")):
            report_menu.addAction(name, lambda value=fmt: self._save_report(value))
        self.report_button.setMenu(report_menu)
        self.report_button.setEnabled(False)
        self.export_button = QPushButton("Export image…")
        self.export_button.clicked.connect(self._export)
        footer.addWidget(self.copy_button)
        footer.addWidget(self.report_button)
        footer.addWidget(self.export_button)
        layout.addLayout(footer)
        self.scroll.setWidget(self.content)
        outer.addWidget(self.scroll)
        self.candidates.hide()
        self.candidate_label.hide()
        self.candidate_controls.hide()
        self.overlay_check.hide()
        from Qt.QtWidgets import QLayout
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        placement = "side"
        try:
            from chimerax.model_panel.tool import ModelPanel
            existing = session.tools.find_by_class(ModelPanel)
            if existing:
                placement = existing[0].tool_window
        except ImportError:
            pass
        self.tool_window.manage(placement=placement)
        parent.setMinimumSize(0, 0)

    def _fill_targets(self, job):
        from .quick_context import primary_models
        blocked = self.target.blockSignals(True)
        self.target.clear()
        explicit = job.context.get("explicit_target", False)
        self.target.addItem(job.context["snapshot"]["target_label"],
                            " ".join(job.context["model_by_spec"]) if explicit else None)
        if explicit:
            self.target.addItem("Use selection / highlighted model", None)
        current = set(job.context["models"])
        for model in primary_models(self.session):
            if model not in current or len(current) > 1:
                self.target.addItem(f"#{model.id_string} · {model.name}", f"#{model.id_string}")
        self.target.setCurrentIndex(0)
        self.target.setToolTip(job.context["snapshot"]["target_label"] + "\n" + job.context["reason"])
        self.target.blockSignals(blocked)

    def show_running(self, job):
        self._restore_comparison(job)
        self.title.setText(job.action.title())
        self._fill_targets(job)
        self.status.setText("Preparing results…")
        self.progress.show()
        self.cancel_button.setEnabled(True)
        self.rerun_button.setEnabled(False)
        self.candidates.setEnabled(False)
        self.candidates.clear()
        self.candidates.hide()
        self.candidate_label.hide()
        self.candidate_controls.hide()
        self.overlay_check.hide()
        self.summary.setPlainText("Calculating for " + job.context["snapshot"]["target_label"])
        self.details.clear()
        self._report_text = ""
        self.copy_button.setEnabled(False)
        self.report_button.setEnabled(False)

    def show_progress(self, text):
        self.status.setText(text)

    def _idle(self):
        self.progress.hide()
        self.cancel_button.setEnabled(False)
        self.rerun_button.setEnabled(self.job is not None)
        self.candidates.setEnabled(True)
        self.undo_button.setEnabled(bool(self.controller and self.controller.undo_states))

    def show_error(self, text):
        self._idle()
        self.status.setText(text)
        self.summary.setPlainText(text)
        self.candidates.hide()
        self.candidate_label.hide()
        self._report_text = ""
        self.copy_button.setEnabled(False)
        self.report_button.setEnabled(False)
        self.candidate_controls.hide()
        self.overlay_check.hide()

    def show_stale(self, text):
        self.progress.hide()
        self.cancel_button.setEnabled(False)
        self.rerun_button.setEnabled(True)
        self.candidates.setEnabled(False)
        self.previous_button.setEnabled(False)
        self.next_button.setEnabled(False)
        self.overlay_check.setEnabled(False)
        self.status.setText(text)
        report_available = bool(self.job is not None and self.job.result is not None and self._report_text)
        self.copy_button.setEnabled(report_available)
        self.report_button.setEnabled(report_available)
        if report_available and not self._report_text.startswith("STALE INPUT:"):
            self._report_text = "STALE INPUT: The structure changed after this calculation.\n\n" + self._report_text

    def show_result(self, job):
        self._restore_comparison(job)
        self._fill_targets(job)
        self._idle()
        result = job.result
        self.title.setText(str(result.get("title", job.action.title())))
        self.status.setText(f"{job.elapsed:.2f} s" + (" · reused result" if job.cached else "") + " · " + job.context["reason"])
        candidates = result.get("candidates", ())
        self.candidates.set_candidates(candidates)
        self.candidates.setVisible(bool(candidates))
        self.candidate_label.setVisible(bool(candidates) or bool(self.candidate_filter.text()))
        self.candidate_controls.setVisible(len(candidates) > 1 or bool(self.candidate_filter.text()))
        self.overlay_check.setVisible(bool(candidates))
        if candidates:
            self.candidates.setCurrentRow(job.candidate)
        self.show_candidate_evidence(result, job.candidate)
        self.copy_button.setEnabled(True)
        self.report_button.setEnabled(True)
        self._filter_candidates(self.candidate_filter.text())

    def show_candidate_evidence(self, result, index):
        lines = list(result.get("details", ()))
        candidates = result.get("candidates", ())
        if 0 <= index < len(candidates):
            candidate = candidates[index]
            lines = [candidate.get("label", "Candidate"), *candidate.get("evidence", ()), "", *lines]
        self.details.setPlainText("\n".join(map(str, lines)))
        metrics = [dict(item) for item in result.get("metrics", ())]
        summary = list(result.get("summary", ()))
        if 0 <= index < len(candidates):
            chosen = candidates[index]
            replacements = {"Focus": chosen.get("label", ""),
                            "Site": str(chosen.get("kind", "")).replace("_", " "),
                            "Lining residues": str(chosen.get("contact_residues", "")),
                            "Volume": f"{_number(chosen['volume']):.0f} Å³" if _number(chosen.get("volume")) is not None else None,
                            "Max depth": f"{_number(chosen['max_depth']):.1f} Å" if _number(chosen.get("max_depth")) is not None else None,
                            "Grid": f"{_number(chosen['grid_step']):.1f} Å" if _number(chosen.get("grid_step")) is not None else None}
            for item in metrics:
                if item.get("label") in replacements:
                    item["value"] = replacements[item["label"]]
            metrics = [item for item in metrics if item.get("value") is not None]
            metrics.append({"label": "Preview", "value": chosen.get("label", "Candidate")})
            if result.get("action") in ("pocket", "cavity", "zoom"):
                summary = list(chosen.get("evidence", ()))[:2]
        rows = "".join(f"<tr><td>{escape(str(item.get('label', '')))}</td><td><b>{escape(str(item.get('value', '')))}</b></td></tr>" for item in metrics)
        bullets = "".join(f"<li>{escape(str(line))}</li>" for line in summary)
        self.summary.setHtml(f"<table cellspacing='5'>{rows}</table><ul>{bullets}</ul>")
        self._report_text = "\n".join([str(result.get("title", "Quick results")),
            self.job.context["snapshot"]["target_label"] if self.job is not None else "",
            *(f"{item.get('label', '')}: {item.get('value', '')}" for item in metrics),
            *map(str, summary), *map(str, result.get("details", ())),
            *(str(c.get("label", "")) + "\n" + "\n".join(map(str, c.get("evidence", ()))) for c in candidates)])
        self.undo_button.setEnabled(bool(self.controller and self.controller.undo_states))
        if self.controller is not None:
            blocked = self.overlay_check.blockSignals(True)
            self.overlay_check.setChecked(bool(getattr(self.controller, "overlay_visible", True)))
            self.overlay_check.blockSignals(blocked)
            available = getattr(self.controller, "has_preview_overlays", lambda: False)()
            self.overlay_check.setEnabled(bool(available) and getattr(self.job, "status", "") == "done")
        self._update_candidate_feedback()

    def _restore_comparison(self, job):
        """Keep only the last comparison's UI choices, never its preview index."""
        saved = getattr(self.session, "_codex_quick_comparison", None)
        models = job.context["models"]
        same_target = (saved is not None and saved["action"] == job.action
                       and len(saved["models"]) == len(models)
                       and all(reference() is model for reference, model
                               in zip(saved["models"], models)))
        self.job = job
        self._comparison_loading = True
        try:
            self.candidate_filter.setText(saved["query"] if same_target else "")
            self.candidates.sortItems(saved["column"] if same_target else 0,
                saved["order"] if same_target else Qt.SortOrder.AscendingOrder)
        finally:
            self._comparison_loading = False
        self._remember_comparison()

    def _remember_comparison(self, *_args):
        if self._disposed or self._comparison_loading or self.job is None:
            return
        self.session._codex_quick_comparison = {
            "action": self.job.action,
            "models": tuple(weakref.ref(model) for model in self.job.context["models"]),
            "query": self.candidate_filter.text(),
            "column": self.candidates.sortColumn(),
            "order": self.candidates.header().sortIndicatorOrder(),
        }

    def _update_candidate_feedback(self):
        if self._disposed:
            return
        visible = self.candidates.visible_items()
        count = len(visible)
        index = getattr(self.job, "candidate", -1)
        preview = self.candidates.item(index)
        if preview is None:
            state = "no preview"
        else:
            state = f"preview #{index + 1}" + (" filtered out" if preview.isHidden() else "")
        total = self.candidates.count()
        matches = (f"{count}/{total} candidates" if count else
                   "No matches" if total else "No candidates")
        self.candidate_label.setText(f"{matches} · {state}")
        enabled = bool(count and (count > 1 or preview not in visible)
                       and self.job is not None and self.job.status == "done")
        self.previous_button.setEnabled(enabled)
        self.next_button.setEnabled(enabled)

    def _candidate_changed(self, item, _previous):
        if self._disposed or self.controller is None or self.job is None or item is None or item.isHidden():
            return
        if self.job.status == "done":
            self.controller.apply_candidate(self.candidates.row(item))

    def _filter_candidates(self, text):
        if self._disposed:
            return
        self.candidates.filter_candidates(text)
        self._update_candidate_feedback()
        self._remember_comparison()

    def _step_candidate(self, direction):
        if self._disposed:
            return
        index = self.candidates.adjacent_index(direction)
        if index is not None:
            self.candidates.setCurrentRow(index)
            self._candidate_changed(self.candidates.item(index), None)

    def _overlay_changed(self, visible):
        if self.controller is not None:
            self.controller.set_overlay_visibility(visible)

    def _details_changed(self, checked):
        if self._disposed:
            return
        self.details.setVisible(checked)
        self.details_toggle.setText("Evidence and limits ▾" if checked else "Evidence and limits ▸")

    def _target_changed(self, _index):
        if self.controller is not None and self.job is not None:
            self.controller.start(self.job.action, self.target.currentData())

    def _rerun(self, _checked=False, *, force=False):
        if self.controller is not None and self.job is not None:
            hint = self.target.currentData() if self.job.context.get("explicit_target") else None
            self.controller.start(self.job.action, model_hint=hint, force=force)

    def _save_report(self, format_name):
        if self._disposed or self.job is None or self.job.result is None or not self.report_button.isEnabled():
            return
        job = self.job
        candidate_index = job.candidate if job.candidate >= 0 else None
        from pathlib import Path
        from Qt.QtWidgets import QFileDialog
        from .quick_reports import build_report, save_report
        from .quick_context import context_valid
        extensions = {"csv": ".csv", "json": ".json", "markdown": ".md"}
        extension = extensions[format_name]
        directory = Path(getattr(self.session, "_codex_quick_report_directory", Path.home() / "Desktop"))
        if not directory.is_dir():
            directory = Path.home()
        path, _filter = QFileDialog.getSaveFileName(self.tool_window.ui_area, "Save analysis report",
            str(directory / (job.action + "-report" + extension)),
            f"{format_name.upper()} report (*{extension})")
        if not path:
            return
        try:
            snapshot = job.context["snapshot"]
            stale = job.status == "stale" or not context_valid(self.session, job.context, check_selection=False)
            report = build_report(job.result, action=job.action, target=snapshot["target_label"],
                candidate_index=candidate_index, elapsed=job.elapsed, cached=job.cached,
                signature=snapshot.get("signature"),
                captured_at=snapshot.get("captured_at", job.context.get("captured_at")), stale=stale)
            saved = save_report(path, report, format_name)
            self.session._codex_quick_report_directory = str(Path(saved).parent)
            if not self._disposed:
                self.status.setText(f"Report saved: {Path(saved).name}" + (" · original input (changed since calculation)" if stale else ""))
        except Exception as error:
            if not self._disposed:
                self.status.setText(f"Report export failed: {error}")

    def _copy_report(self):
        if self._disposed or not self._report_text or not self.copy_button.isEnabled():
            return
        from Qt.QtWidgets import QApplication
        QApplication.clipboard().setText(self._report_text)
        self.status.setText("Report copied.")

    def _export(self):
        if self._disposed:
            return
        from .camera_bookmarks import CameraBookmarks
        tool = CameraBookmarks.get_singleton(self.session)
        reveal = getattr(tool, "show_image_export", None)
        if callable(reveal):
            reveal()
        else:
            # A targeted Quick toolbar reload can retain an older bookmark
            # instance. Its export controls already support native scrolling.
            tool.display(True)
            tool.tool_window._dock_widget.raise_()
            target = (tool.export_group if tool.export_group.height() + 12
                      <= tool.scroll_area.viewport().height() else tool.export_button)
            tool.scroll_area.ensureWidgetVisible(target, 0, 6)

    def _dispose(self, *, native_destroyed=False):
        if native_destroyed:
            self._native_destroyed = True
        if self._disposed:
            return
        self._disposed = True
        controller, self.controller = self.controller, None
        if controller is not None and getattr(controller, "panel", None) is self:
            # Detach before cancellation; closing a native dock must not emit
            # progress/error updates into its disappearing child widgets.
            controller.panel = None
            controller.cancel_active()

    def delete(self):
        if self._delete_called:
            return
        self._delete_called = True
        self._dispose()
        if self._native_destroyed:
            from .ui_theme import forget_destroyed_tool_window
            forget_destroyed_tool_window(self)
        super().delete()
