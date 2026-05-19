"""Camera Bookmarks tool: save and restore the 3D view camera angle.

Wraps ChimeraX's built-in `view name X` / `view X` / `view delete X` commands
in a tiny side panel with a one-click list. Perfect for keeping a fixed
viewpoint while toggling models for screenshots, comparisons, etc.
"""

from chimerax.core.tools import ToolInstance


def _list_named_views(session):
    """Return the names of currently saved views as known to ChimeraX.
    Named views live on session._named_views.views (a NamedViews instance,
    see chimerax.std_commands.view._named_views)."""
    try:
        from chimerax.std_commands.view import _named_views as _nv_helper
        nvs = _nv_helper(session)
        if nvs is not None and hasattr(nvs, "views"):
            return list(nvs.views.keys())
    except Exception:
        pass
    # Direct attribute fallback
    try:
        nvs = getattr(session, "_named_views", None)
        if nvs is not None and hasattr(nvs, "views"):
            return list(nvs.views.keys())
    except Exception:
        pass
    # Last-resort fallback: parse `view list` output.
    try:
        from chimerax.core.commands import run as cx_run
        result = cx_run(session, "view list", log=False)
        if isinstance(result, list):
            return [str(x) for x in result]
        if isinstance(result, str):
            names = [s.strip() for s in result.replace(",", "\n").splitlines()]
            return [n for n in names if n]
    except Exception:
        pass
    return []


class CameraBookmarks(ToolInstance):

    SESSION_ENDURING = False
    # The actual camera bookmarks are ChimeraX named views, which are already
    # session-managed by ChimeraX. This panel is only a controller for them, so
    # saving the ToolInstance itself just adds a fragile restore dependency.
    SESSION_SAVE = False
    help = None

    def __init__(self, session, tool_name):
        super().__init__(session, tool_name)

        from chimerax.ui import MainToolWindow
        self.tool_window = MainToolWindow(self)
        parent = self.tool_window.ui_area

        from Qt.QtWidgets import (
            QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
            QListWidget, QFrame, QLineEdit, QInputDialog,
            QSizePolicy, QComboBox, QGridLayout,
        )
        from Qt.QtGui import QFont
        from Qt.QtCore import Qt

        outer = QVBoxLayout()
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(6)

        title = QLabel("Camera Bookmarks")
        f = QFont(); f.setBold(True); f.setPointSize(13)
        title.setFont(f)
        outer.addWidget(title)

        sub = QLabel(
            "Rotate the view in fixed steps and save / restore camera angles."
        )
        sub.setStyleSheet("color: #666;")
        sub.setWordWrap(True)
        outer.addWidget(sub)

        # ---- Rotation controls ----
        rot_label = QLabel("Rotate around target:")
        rotf = QFont(); rotf.setBold(True); rot_label.setFont(rotf)
        outer.addWidget(rot_label)

        cofr_row = QHBoxLayout()
        cofr_lab = QLabel("Pivot:"); cofr_lab.setMinimumWidth(46)
        cofr_row.addWidget(cofr_lab)
        self.cofr_combo = QComboBox()
        self.cofr_combo.addItems([
            "Current (no change)",
            "Selection",
            "All models (front center)",
        ])
        self.cofr_combo.setToolTip(
            "Where to pivot when rotating. 'Selection' first runs `cofr sel`, "
            "so picking a residue or chain locks the rotation around it.")
        cofr_row.addWidget(self.cofr_combo, 1)
        outer.addLayout(cofr_row)

        step_row = QHBoxLayout()
        step_lab = QLabel("Step:"); step_lab.setMinimumWidth(46)
        step_row.addWidget(step_lab)
        self.step_combo = QComboBox()
        self.step_combo.addItems(["15°", "30°", "45°", "90°", "180°"])
        self.step_combo.setCurrentText("90°")
        step_row.addWidget(self.step_combo)
        step_row.addStretch(1)
        outer.addLayout(step_row)

        rot_grid = QGridLayout()
        rot_grid.setSpacing(4)

        def _rot_btn(label, axis, sign):
            btn = QPushButton(label)
            btn.setMinimumHeight(28)
            btn.clicked.connect(lambda *_a, a=axis, s=sign: self._rotate(a, s))
            return btn

        rot_grid.addWidget(QLabel("X"), 0, 0)
        rot_grid.addWidget(_rot_btn("← X-", "x", -1), 0, 1)
        rot_grid.addWidget(_rot_btn("X+ →", "x",  1), 0, 2)
        rot_grid.addWidget(QLabel("Y"), 1, 0)
        rot_grid.addWidget(_rot_btn("← Y-", "y", -1), 1, 1)
        rot_grid.addWidget(_rot_btn("Y+ →", "y",  1), 1, 2)
        rot_grid.addWidget(QLabel("Z"), 2, 0)
        rot_grid.addWidget(_rot_btn("↺ Z-", "z", -1), 2, 1)
        rot_grid.addWidget(_rot_btn("Z+ ↻", "z",  1), 2, 2)
        for c in (1, 2):
            rot_grid.setColumnStretch(c, 1)
        outer.addLayout(rot_grid)

        quick_row = QHBoxLayout()
        front_btn = QPushButton("Reset orient")
        front_btn.setToolTip("Reset to default orientation (view orient).")
        front_btn.clicked.connect(self._reset_orient)
        quick_row.addWidget(front_btn)
        center_btn = QPushButton("Center on selection")
        center_btn.setToolTip("`cofr sel` then `view sel` -- focus the selection.")
        center_btn.clicked.connect(self._center_on_selection)
        quick_row.addWidget(center_btn)
        outer.addLayout(quick_row)

        # ---- Divider ----
        line0 = QFrame(); line0.setFrameShape(QFrame.HLine); line0.setFrameShadow(QFrame.Sunken)
        outer.addWidget(line0)

        # ---- Screenshot helpers ----
        legend_label = QLabel("Screenshot helpers:")
        legf = QFont(); legf.setBold(True); legend_label.setFont(legf)
        outer.addWidget(legend_label)

        legend_row = QHBoxLayout()
        self.legend_combo = QComboBox()
        self.legend_combo.addItems([
            "Hydrophobicity (mlp: darkcyan→white→darkgoldenrod)",
            "Hydrophobicity (Kyte-Doolittle: blue→white→red)",
            "Conservation (red→white)",
            "Custom (blue→white→red)",
        ])
        legend_row.addWidget(self.legend_combo, 1)
        outer.addLayout(legend_row)

        legend_btn_row = QHBoxLayout()
        add_legend_btn = QPushButton("Add legend")
        add_legend_btn.setStyleSheet(
            "QPushButton { background-color: #16a34a; color: white; "
            "border-radius: 4px; padding: 6px 12px; }"
            "QPushButton:hover { background-color: #15803d; }"
        )
        add_legend_btn.clicked.connect(self._add_legend)
        legend_btn_row.addWidget(add_legend_btn)
        clear_legend_btn = QPushButton("Clear legend")
        clear_legend_btn.clicked.connect(self._clear_legend)
        legend_btn_row.addWidget(clear_legend_btn)
        legend_btn_row.addStretch(1)
        outer.addLayout(legend_btn_row)

        # ---- Divider ----
        line1 = QFrame(); line1.setFrameShape(QFrame.HLine); line1.setFrameShadow(QFrame.Sunken)
        outer.addWidget(line1)

        # ---- Save row ----
        save_row = QHBoxLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Name (auto if blank)")
        self.name_input.returnPressed.connect(self._save_view)
        save_row.addWidget(self.name_input, 1)
        self.save_btn = QPushButton("Save Current View")
        big = QFont(); big.setBold(True); big.setPointSize(11)
        self.save_btn.setFont(big)
        self.save_btn.setMinimumHeight(34)
        self.save_btn.setStyleSheet(
            "QPushButton { background-color: #2563eb; color: white; "
            "border-radius: 5px; padding: 6px 14px; }"
            "QPushButton:hover { background-color: #1d4ed8; }"
        )
        self.save_btn.clicked.connect(self._save_view)
        save_row.addWidget(self.save_btn)
        outer.addLayout(save_row)

        # ---- Divider ----
        line = QFrame(); line.setFrameShape(QFrame.HLine); line.setFrameShadow(QFrame.Sunken)
        outer.addWidget(line)

        # ---- List ----
        list_label = QLabel("Click a name to restore:")
        outer.addWidget(list_label)
        self.list_widget = QListWidget()
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.itemDoubleClicked.connect(self._on_item_clicked)
        self.list_widget.setMinimumHeight(180)
        self.list_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        outer.addWidget(self.list_widget, 1)

        # ---- Actions ----
        actions_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh_list)
        actions_row.addWidget(self.refresh_btn)
        self.rename_btn = QPushButton("Rename")
        self.rename_btn.clicked.connect(self._rename_selected)
        actions_row.addWidget(self.rename_btn)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setStyleSheet(
            "QPushButton { color: #b91c1c; }"
        )
        self.delete_btn.clicked.connect(self._delete_selected)
        actions_row.addWidget(self.delete_btn)
        self.delete_all_btn = QPushButton("Clear All")
        self.delete_all_btn.clicked.connect(self._delete_all)
        actions_row.addWidget(self.delete_all_btn)
        actions_row.addStretch(1)
        outer.addLayout(actions_row)

        # ---- Status ----
        self.status = QLabel("")
        self.status.setStyleSheet("color: #555; font-size: 10px;")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        parent.setLayout(outer)
        self._refresh_list()
        self.tool_window.manage(placement="side")

    # ---------------------------------------------------------------- helpers

    def _run(self, command, status_ok=None, status_err_prefix="Error"):
        from chimerax.core.commands import run as cx_run
        try:
            cx_run(self.session, command)
            if status_ok is not None:
                self._set_status(status_ok)
            return True
        except Exception as exc:
            self._set_status(f"{status_err_prefix}: {exc}", err=True)
            return False

    def _set_status(self, msg, err=False):
        self.status.setText(msg)
        self.status.setStyleSheet(
            "color: #b91c1c; font-size: 10px;" if err
            else "color: #166534; font-size: 10px;"
        )

    def _next_auto_name(self):
        existing = set(_list_named_views(self.session))
        i = 1
        while f"View {i}" in existing:
            i += 1
        return f"View {i}"

    # ---------------------------------------------------------------- rotation

    def _apply_pivot(self):
        """Apply the chosen center-of-rotation before issuing a `turn`."""
        from chimerax.core.commands import run as cx_run
        choice = self.cofr_combo.currentText()
        if choice.startswith("Selection"):
            try:
                cx_run(self.session, "cofr sel")
            except Exception as exc:
                self._set_status(f"cofr sel failed (no selection?): {exc}", err=True)
                return False
        elif choice.startswith("All models"):
            try:
                cx_run(self.session, "cofr center")
            except Exception:
                pass
        # "Current (no change)" -> leave cofr alone
        return True

    def _step_degrees(self):
        text = self.step_combo.currentText().rstrip("°").strip()
        try:
            return float(text)
        except Exception:
            return 90.0

    def _rotate(self, axis, sign):
        if not self._apply_pivot():
            return
        deg = self._step_degrees() * sign
        self._run(f"turn {axis} {deg}",
                  status_ok=f"Rotated {axis.upper()} {deg:+g}°",
                  status_err_prefix="Turn failed")

    def _reset_orient(self):
        self._run("view orient",
                  status_ok="Orientation reset (view orient).",
                  status_err_prefix="Reset failed")

    def _center_on_selection(self):
        from chimerax.core.commands import run as cx_run
        try:
            cx_run(self.session, "cofr sel")
            cx_run(self.session, "view sel")
            self._set_status("Centered on selection.")
        except Exception as exc:
            self._set_status(f"Center on selection failed: {exc}", err=True)

    # ---------------------------------------------------------------- legend

    def _add_legend(self):
        choice = self.legend_combo.currentText()
        # Common positioning: bottom-left, ~40% wide, slim band.
        common_opts = (
            " pos 0.05,0.06 size 0.35,0.04 fontSize 14 "
            "labelOffset 4 numericLabelSpacing equal "
            "colorTreatment blended ticks true tickThickness 1.5 "
            "labelColor black"
        )
        if choice.startswith("Hydrophobicity (mlp"):
            # Exact ChimeraX `lipophilicity` palette colours.
            cmd = ('key darkcyan:"Hydrophilic" white:0 darkgoldenrod:"Lipophilic"'
                   + common_opts + ' title "Hydrophobicity (MLP)"')
        elif choice.startswith("Hydrophobicity (Kyte"):
            cmd = ('key blue:"-4.5" white:0 red:"+4.5"'
                   + common_opts + ' title "Hydrophobicity (Kyte-Doolittle)"')
        elif choice.startswith("Conservation"):
            cmd = ('key red:"Conserved" orange:"" yellow:"" white:"Variable"'
                   + common_opts + ' title "Conservation"')
        else:
            # Custom: fall back to a generic 3-stop placeholder users can edit.
            cmd = ('key blue:"Low" white:"" red:"High"'
                   + common_opts + ' title "Custom"')
        self._run(cmd, status_ok=f"Added legend: {choice}",
                  status_err_prefix="Add legend failed")

    def _clear_legend(self):
        self._run("~key", status_ok="Legend removed.",
                  status_err_prefix="Clear failed")

    # ---------------------------------------------------------------- actions

    def _save_view(self):
        name = self.name_input.text().strip()
        if not name:
            name = self._next_auto_name()
        # ChimeraX `view name X` saves the current camera + cofr under the name X.
        if self._run(f"view name {name}",
                     status_ok=f"Saved view: {name}"):
            self.name_input.clear()
            self._refresh_list()
            # Highlight the just-saved item.
            for i in range(self.list_widget.count()):
                item = self.list_widget.item(i)
                if item.text() == name:
                    self.list_widget.setCurrentItem(item)
                    break

    def _on_item_clicked(self, item):
        name = item.text()
        self._run(f"view {name}", status_ok=f"Restored: {name}",
                  status_err_prefix="Restore failed")

    def _delete_selected(self):
        item = self.list_widget.currentItem()
        if item is None:
            return
        name = item.text()
        if self._run(f"view delete {name}",
                     status_ok=f"Deleted: {name}",
                     status_err_prefix="Delete failed"):
            self._refresh_list()

    def _delete_all(self):
        names = _list_named_views(self.session)
        if not names:
            return
        from Qt.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self.tool_window.ui_area, "Clear all bookmarks",
            f"Delete all {len(names)} saved views?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok = 0
        for name in names:
            if self._run(f"view delete {name}", status_err_prefix="Delete failed"):
                ok += 1
        self._set_status(f"Cleared {ok}/{len(names)} bookmarks.")
        self._refresh_list()

    def _rename_selected(self):
        from Qt.QtWidgets import QInputDialog
        item = self.list_widget.currentItem()
        if item is None:
            self._set_status("Pick a saved view first.", err=True)
            return
        old_name = item.text()
        new_name, ok = QInputDialog.getText(
            self.tool_window.ui_area, "Rename view", "New name:", text=old_name
        )
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == old_name:
            return
        # Restore the old view, save under new name, delete old name.
        from chimerax.core.commands import run as cx_run
        try:
            cx_run(self.session, f"view {old_name}")
            cx_run(self.session, f"view name {new_name}")
            cx_run(self.session, f"view delete {old_name}")
            self._set_status(f"Renamed: {old_name} -> {new_name}")
            self._refresh_list()
        except Exception as exc:
            self._set_status(f"Rename failed: {exc}", err=True)

    def _refresh_list(self):
        names = sorted(_list_named_views(self.session))
        self.list_widget.clear()
        for n in names:
            self.list_widget.addItem(n)
        if not names:
            self._set_status("No bookmarks yet. Adjust your view, then click Save.")
