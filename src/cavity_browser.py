from chimerax.core.tools import ToolInstance, get_singleton


DEFAULT_CAVITY_COLORS = (
    "#ff9d5a",
    "#ffd166",
    "#06d6a0",
    "#118ab2",
    "#a36bd9",
    "#5b8fb9",
)


def open_cavity_browser(
    session,
    candidates=None,
    *,
    selected_ranks=None,
    transparency=None,
    shell_distance=None,
    strategy_label=None,
):
    if candidates is not None:
        set_cavity_candidates(
            session,
            candidates,
            selected_ranks=selected_ranks,
            transparency=transparency,
            shell_distance=shell_distance,
            strategy_label=strategy_label,
        )
    if not getattr(getattr(session, "ui", None), "is_gui", False):
        return None
    tool = CodexCavityBrowser.get_singleton(session, create=True, display=True)
    if tool is not None:
        tool.display(True)
        tool.reload_from_session()
    return tool


def set_cavity_candidates(
    session,
    candidates,
    *,
    selected_ranks=None,
    transparency=None,
    shell_distance=None,
    strategy_label=None,
):
    records = _normalize_candidates(candidates)
    session._codex_cavity_candidates = records
    session._codex_cavity_selected_ranks = _normalize_rank_list(selected_ranks) or (
        [records[0]["rank"]] if records else []
    )
    if transparency is not None:
        try:
            session._codex_cavity_transparency = max(0, min(100, int(transparency)))
        except Exception:
            pass
    elif not hasattr(session, "_codex_cavity_transparency"):
        session._codex_cavity_transparency = 65
    if shell_distance is not None:
        try:
            session._codex_cavity_shell_distance = float(shell_distance)
        except Exception:
            pass
    if strategy_label is not None:
        session._codex_cavity_strategy_label = str(strategy_label)
    return records


def apply_cavity_display(
    session,
    selected_ranks=None,
    *,
    transparency=None,
    select_lining=False,
):
    candidates = list(getattr(session, "_codex_cavity_candidates", []) or [])
    if not candidates:
        return "No cavity candidates are loaded. Run the Cavity action first."

    if selected_ranks is None:
        selected_ranks = getattr(session, "_codex_cavity_selected_ranks", [])
    rank_set = set(_normalize_rank_list(selected_ranks))
    valid = {int(candidate["rank"]) for candidate in candidates}
    rank_set = {rank for rank in rank_set if rank in valid}

    if transparency is None:
        transparency = getattr(session, "_codex_cavity_transparency", 65)
    try:
        transparency = max(0, min(100, int(transparency)))
    except Exception:
        transparency = 65

    for candidate in candidates:
        spec = str(candidate.get("spec") or "").strip()
        if not spec:
            continue
        _run(session, f"~surface {spec}")
        _run(session, f"~label {spec}")

    shown = []
    selected_specs = []
    for candidate in candidates:
        rank = int(candidate["rank"])
        if rank not in rank_set:
            continue
        spec = str(candidate.get("spec") or "").strip()
        if not spec:
            continue
        color = str(candidate.get("color") or DEFAULT_CAVITY_COLORS[(rank - 1) % len(DEFAULT_CAVITY_COLORS)])
        _run(session, f"surface {spec}")
        _run(session, f"color {spec} {color} target s")
        _run(session, f"transparency {spec} {transparency} target s")
        shown.append(candidate)
        selected_specs.append(spec)

    if select_lining and selected_specs:
        selection_spec = " ".join(selected_specs)
        _run(session, f"select {selection_spec}")
        _run(session, f"show {selection_spec} atoms")
        _run(session, f"style {selection_spec} stick")

    session._codex_cavity_selected_ranks = sorted(rank_set)
    session._codex_cavity_transparency = transparency
    session._codex_cavity_managed_specs = selected_specs
    session._codex_bridge_cavity_spec = selected_specs[0] if selected_specs else ""

    if not shown:
        return "All cavity surfaces hidden."
    rank_text = ", ".join(f"#{candidate['rank']}" for candidate in shown)
    return f"Cavity surface display updated: showing {rank_text} at {transparency}% transparency."


class CodexCavityBrowser(ToolInstance):
    SESSION_ENDURING = False
    SESSION_SAVE = False
    UI_LAYOUT_VERSION = 1
    help = "help:user/tools/codex_assistant.html"

    @classmethod
    def get_singleton(cls, session, create=True, display=True, **kw):
        instance = get_singleton(session, cls, "Cavity Browser", create=create, display=display, **kw)
        if instance is not None and getattr(instance, "_ui_layout_version", None) != cls.UI_LAYOUT_VERSION:
            try:
                instance.delete()
            except Exception:
                pass
            instance = get_singleton(session, cls, "Cavity Browser", create=create, display=display, **kw)
        return instance

    def __init__(self, session, tool_name):
        super().__init__(session, tool_name)
        self._ui_layout_version = self.UI_LAYOUT_VERSION
        self._rank_by_row = []
        self._updating = False
        from chimerax.ui import MainToolWindow

        self.tool_window = MainToolWindow(self, close_destroys=True)
        self._build_ui()

    def _build_ui(self):
        from Qt.QtCore import Qt
        from Qt.QtWidgets import (
            QAbstractItemView,
            QHBoxLayout,
            QLabel,
            QListWidget,
            QPushButton,
            QSpinBox,
            QVBoxLayout,
        )

        parent = self.tool_window.ui_area
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        parent.setLayout(layout)
        parent.setStyleSheet(
            "QWidget { background: #171a1d; color: #e5e8ec; }"
            "QLabel { background: transparent; border: none; }"
            "QListWidget { background: #101417; color: #eef1f4; border: 1px solid #344150; border-radius: 7px; }"
            "QPushButton { background: #20262d; color: #eef1f4; border: 1px solid #344150; border-radius: 7px; padding: 5px 9px; }"
            "QPushButton:hover { background: #29313a; border-color: #5c6a78; }"
            "QSpinBox { background: #101417; color: #eef1f4; border: 1px solid #344150; border-radius: 7px; padding: 4px 7px; }"
        )

        title = QLabel("Cavity candidates", parent)
        title_font = title.font()
        title_font.setPointSize(13)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        self.summary_label = QLabel("", parent)
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        controls = QHBoxLayout()
        layout.addLayout(controls)
        controls.addWidget(QLabel("Surface", parent))
        self.transparency_spin = QSpinBox(parent)
        self.transparency_spin.setRange(0, 100)
        self.transparency_spin.setSingleStep(5)
        self.transparency_spin.setSuffix(" %")
        self.transparency_spin.valueChanged.connect(self._transparency_changed)
        controls.addWidget(self.transparency_spin)

        self.show_all_button = QPushButton("Show All", parent)
        self.show_all_button.clicked.connect(self.show_all)
        controls.addWidget(self.show_all_button)

        self.hide_all_button = QPushButton("Hide All", parent)
        self.hide_all_button.clicked.connect(self.hide_all)
        controls.addWidget(self.hide_all_button)

        self.list_widget = QListWidget(parent)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_widget.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.list_widget, 1)

        bottom = QHBoxLayout()
        layout.addLayout(bottom)
        self.select_lining_button = QPushButton("Select Lining", parent)
        self.select_lining_button.clicked.connect(self.select_lining)
        bottom.addWidget(self.select_lining_button)

        self.refresh_button = QPushButton("Refresh", parent)
        self.refresh_button.clicked.connect(self.reload_from_session)
        bottom.addWidget(self.refresh_button)

        self.tool_window.manage(placement="side")
        self.reload_from_session()

    def reload_from_session(self):
        candidates = list(getattr(self.session, "_codex_cavity_candidates", []) or [])
        selected = set(_normalize_rank_list(getattr(self.session, "_codex_cavity_selected_ranks", [])))
        transparency = int(getattr(self.session, "_codex_cavity_transparency", 65) or 65)
        strategy = str(getattr(self.session, "_codex_cavity_strategy_label", "") or "")
        self._updating = True
        try:
            self.list_widget.clear()
            self._rank_by_row = []
            for candidate in candidates:
                rank = int(candidate.get("rank", len(self._rank_by_row) + 1) or (len(self._rank_by_row) + 1))
                self._rank_by_row.append(rank)
                self.list_widget.addItem(_candidate_label(candidate))
                item = self.list_widget.item(self.list_widget.count() - 1)
                if rank in selected:
                    item.setSelected(True)
            if candidates and not self.list_widget.selectedItems():
                self.list_widget.item(0).setSelected(True)
                self.session._codex_cavity_selected_ranks = [self._rank_by_row[0]]
            self.transparency_spin.setValue(max(0, min(100, transparency)))
            self.summary_label.setText(
                f"{strategy}: {len(candidates)} candidate(s). Click a row to show only that cavity surface."
                if strategy
                else f"{len(candidates)} candidate(s). Click a row to show only that cavity surface."
            )
        finally:
            self._updating = False

    def displayed(self):
        dock_widget = getattr(self.tool_window, "_dock_widget", None)
        if dock_widget is not None:
            return bool(dock_widget.isVisible())
        ui_area = getattr(self.tool_window, "ui_area", None)
        return bool(ui_area is not None and ui_area.isVisible())

    def _selection_changed(self):
        if self._updating:
            return
        ranks = self._selected_ranks()
        self.session._codex_cavity_selected_ranks = ranks
        try:
            message = apply_cavity_display(
                self.session,
                ranks,
                transparency=self.transparency_spin.value(),
            )
            self.summary_label.setText(message)
        except Exception as err:
            self.summary_label.setText(f"Cavity surface update failed: {err}")

    def _transparency_changed(self, value):
        if self._updating:
            return
        try:
            message = apply_cavity_display(self.session, self._selected_ranks(), transparency=int(value))
            self.summary_label.setText(message)
        except Exception as err:
            self.summary_label.setText(f"Cavity transparency update failed: {err}")

    def _selected_ranks(self):
        selected = []
        for row in range(self.list_widget.count()):
            if self.list_widget.item(row).isSelected():
                selected.append(self._rank_by_row[row])
        return selected

    def show_all(self):
        self._updating = True
        try:
            for row in range(self.list_widget.count()):
                self.list_widget.item(row).setSelected(True)
        finally:
            self._updating = False
        self._selection_changed()

    def hide_all(self):
        self._updating = True
        try:
            self.list_widget.clearSelection()
        finally:
            self._updating = False
        self._selection_changed()

    def select_lining(self):
        try:
            message = apply_cavity_display(
                self.session,
                self._selected_ranks(),
                transparency=self.transparency_spin.value(),
                select_lining=True,
            )
            self.summary_label.setText(message)
        except Exception as err:
            self.summary_label.setText(f"Cavity lining selection failed: {err}")


def _normalize_candidates(candidates):
    records = []
    for index, candidate in enumerate(candidates or [], start=1):
        if not isinstance(candidate, dict):
            continue
        try:
            rank = int(candidate.get("rank", index) or index)
        except Exception:
            rank = index
        spec = str(candidate.get("spec") or "").strip()
        if not spec:
            continue
        records.append({
            "rank": rank,
            "spec": spec,
            "specs_list": list(candidate.get("specs_list") or spec.split()),
            "color": str(candidate.get("color") or DEFAULT_CAVITY_COLORS[(index - 1) % len(DEFAULT_CAVITY_COLORS)]),
            "group_name": str(candidate.get("group_name") or f"cavity_{rank:02d}"),
            "choice_label": str(candidate.get("choice_label") or ""),
            "descriptor": str(candidate.get("descriptor") or ""),
            "tags": list(candidate.get("tags") or []),
            "residue_count": int(candidate.get("residue_count", len(spec.split())) or len(spec.split())),
            "volume": candidate.get("volume"),
            "max_depth": candidate.get("max_depth"),
            "rank_score": candidate.get("rank_score"),
        })
    return records


def _normalize_rank_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        values = value.replace(",", " ").split()
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = [value]
    ranks = []
    for item in values:
        try:
            rank = int(item)
        except Exception:
            continue
        if rank not in ranks:
            ranks.append(rank)
    return ranks


def _candidate_label(candidate):
    if candidate.get("choice_label"):
        return str(candidate["choice_label"])
    rank = int(candidate.get("rank", 0) or 0)
    parts = [f"#{rank}"]
    if candidate.get("rank_score") is not None:
        try:
            parts.append(f"score {float(candidate['rank_score']):.2f}")
        except Exception:
            pass
    if candidate.get("volume") is not None:
        try:
            parts.append(f"volume {float(candidate['volume']):.0f} A^3")
        except Exception:
            pass
    if candidate.get("max_depth") is not None:
        try:
            parts.append(f"depth {float(candidate['max_depth']):.1f} A")
        except Exception:
            pass
    parts.append(f"{int(candidate.get('residue_count', 0) or 0)} residues")
    tags = ", ".join(candidate.get("tags") or [])
    if tags:
        parts.append(tags)
    return "; ".join(parts)


def _run(session, command):
    from chimerax.core.commands import run

    return run(session, command)
