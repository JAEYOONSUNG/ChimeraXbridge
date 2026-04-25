import threading

from Qt.QtCore import Qt, QTimer
from Qt.QtGui import QColor, QFontDatabase, QTextCharFormat, QTextCursor
from Qt.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from chimerax.core.tools import ToolInstance, get_singleton

from .integration import command_batch


AA3_TO_1 = {
    "ALA": "A",
    "ARG": "R",
    "ASN": "N",
    "ASP": "D",
    "CYS": "C",
    "GLN": "Q",
    "GLU": "E",
    "GLY": "G",
    "HIS": "H",
    "ILE": "I",
    "LEU": "L",
    "LYS": "K",
    "MET": "M",
    "PHE": "F",
    "PRO": "P",
    "SER": "S",
    "THR": "T",
    "TRP": "W",
    "TYR": "Y",
    "VAL": "V",
    "SEC": "U",
    "PYL": "O",
    "ASX": "B",
    "GLX": "Z",
    "MSE": "M",
    "UNK": "X",
}


class ClickableSequenceText(QPlainTextEdit):
    """A fixed-width sequence strip where every character maps to a residue."""

    def __init__(self, click_callback, context_callback, parent=None):
        super().__init__(parent)
        self._click_callback = click_callback
        self._context_callback = context_callback
        self._sequence_length = 0
        self._line_starts = []
        self._tooltips = []
        self._drag_anchor = None
        self._drag_current = None
        self._highlight_ranges = []
        self._preview_range = None
        self.setReadOnly(True)
        self.setMouseTracking(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setViewportMargins(0, 0, 0, 10)
        self.setFixedHeight(70)
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.setPlaceholderText("Load a protein model to show a clickable sequence.")

    def set_sequence_text(self, lines, sequence_length, tooltips):
        text = "\n".join(lines)
        starts = []
        position = 0
        for line in lines:
            starts.append(position)
            position += len(line) + 1
        self._sequence_length = int(sequence_length or 0)
        self._line_starts = starts
        self._tooltips = list(tooltips or [])
        self.setPlainText(text)
        self._drag_anchor = None
        self._drag_current = None
        self._preview_range = None
        self._apply_highlights()

    def mousePressEvent(self, event):
        context_click = (
            event.button() == Qt.MouseButton.RightButton
            or (
                event.button() == Qt.MouseButton.LeftButton
                and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            )
        )
        if context_click:
            index = self._residue_index_at(event.pos())
            if index is not None:
                self.preview_range(index, index)
                self._drag_anchor = None
                self._drag_current = None
                self._context_callback(index, self._event_global_pos(event))
                event.accept()
                return
        if event.button() == Qt.MouseButton.LeftButton:
            index = self._residue_index_at(event.pos())
            if index is not None:
                self._drag_anchor = index
                self._drag_current = index
                self.preview_range(index, index)
                event.accept()
                return
        super().mousePressEvent(event)

    def _event_global_pos(self, event):
        if hasattr(event, "globalPosition"):
            try:
                return event.globalPosition().toPoint()
            except Exception:
                pass
        if hasattr(event, "globalPos"):
            try:
                return event.globalPos()
            except Exception:
                pass
        return self.mapToGlobal(event.pos())

    def mouseMoveEvent(self, event):
        index = self._residue_index_at(event.pos())
        if self._drag_anchor is not None and bool(event.buttons() & Qt.MouseButton.LeftButton):
            if index is not None:
                self._drag_current = index
                self.preview_range(self._drag_anchor, self._drag_current)
            event.accept()
            return
        if index is None:
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
            self.setToolTip("")
        else:
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            if index < len(self._tooltips):
                self.setToolTip(self._tooltips[index])
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_anchor is not None:
            index = self._residue_index_at(event.pos())
            if index is not None:
                self._drag_current = index
            start = self._drag_anchor
            end = self._drag_current if self._drag_current is not None else start
            self._drag_anchor = None
            self._drag_current = None
            self.preview_range(None, None)
            self._click_callback(start, end)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if self._drag_anchor is None:
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
            self.setToolTip("")
        super().leaveEvent(event)

    def _residue_index_at(self, pos):
        if self._sequence_length <= 0:
            return None
        cursor = self.cursorForPosition(pos)
        position = cursor.position()
        for start in self._line_starts:
            col = position - start
            if 0 <= col < self._sequence_length:
                return col
        return None

    def _highlight_residue(self, index):
        self.highlight_range(index, index)

    def highlight_range(self, start, end):
        self.highlight_ranges([(start, end)])

    def highlight_ranges(self, ranges):
        self._highlight_ranges = self._normalize_ranges(ranges)
        self._preview_range = None
        self._apply_highlights()

    def preview_range(self, start, end):
        if start is None or end is None:
            self._preview_range = None
        else:
            normalized = self._normalize_ranges([(start, end)])
            self._preview_range = normalized[0] if normalized else None
        self._apply_highlights()

    def _normalize_ranges(self, ranges):
        if not self._line_starts or self._sequence_length <= 0:
            return []
        normalized = []
        for start, end in ranges or []:
            start = max(0, min(int(start), self._sequence_length - 1))
            end = max(0, min(int(end), self._sequence_length - 1))
            if end < start:
                start, end = end, start
            normalized.append((start, end))
        return _merge_index_ranges(normalized)

    def _apply_highlights(self):
        if not self._line_starts or self._sequence_length <= 0:
            self.setExtraSelections([])
            return
        sequence_line_start = self._line_starts[1] if len(self._line_starts) > 1 else self._line_starts[0]
        format_selected = QTextCharFormat()
        format_selected.setBackground(QColor("#d5d9de"))
        format_selected.setForeground(QColor("#0b0d0f"))
        format_preview = QTextCharFormat()
        format_preview.setBackground(QColor("#f0f2f4"))
        format_preview.setForeground(QColor("#0b0d0f"))

        selections = []
        ranges = list(self._highlight_ranges)
        if self._preview_range is not None:
            ranges = _merge_index_ranges(ranges + [self._preview_range])
        for start, end in ranges:
            cursor = QTextCursor(self.document())
            cursor.setPosition(sequence_line_start + start)
            cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, end - start + 1)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = format_preview if self._preview_range is not None and start <= self._preview_range[1] and end >= self._preview_range[0] else format_selected
            selections.append(selection)
        self.setExtraSelections(selections)


class CodexSequenceBar(ToolInstance):

    SESSION_ENDURING = False
    SESSION_SAVE = False
    help = "help:user/tools/codex_assistant.html"
    UI_LAYOUT_VERSION = 12

    @classmethod
    def get_singleton(cls, session, create=True, display=True):
        instance = get_singleton(session, cls, "Sequence Bar", create=create, display=display)
        if instance is not None and getattr(instance, "_ui_layout_version", None) != cls.UI_LAYOUT_VERSION:
            try:
                instance.delete()
            except Exception:
                pass
            instance = get_singleton(session, cls, "Sequence Bar", create=create, display=display)
        return instance

    def __init__(self, session, tool_name):
        super().__init__(session, tool_name)
        self._ui_layout_version = self.UI_LAYOUT_VERSION
        self._entries = []
        self._current_entry = None
        self._selected_ranges = []
        self._selection_click_mode = "residue"
        self._refresh_pending = False
        self._wrapper = None
        self._previous_main_view = None
        self.bar_widget = None
        self.handlers = []
        self._build_ui()

    def _build_ui(self):
        parent = QWidget(self.session.ui.main_window)
        parent.setObjectName("codex_sequence_bar")
        self.bar_widget = parent
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 5, 8, 6)
        layout.setSpacing(4)
        parent.setLayout(layout)
        parent.setMinimumHeight(104)
        parent.setMaximumHeight(136)
        parent.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        parent.setStyleSheet(
            "QWidget#codex_sequence_bar { background: #141618; border-bottom: 1px solid #3a4046; }"
            "QLabel { color: #e4e7eb; }"
            "QComboBox {"
            " background: #101214;"
            " color: #edf0f3;"
            " border: 1px solid #3a4046;"
            " border-radius: 7px;"
            " padding: 4px 8px;"
            "}"
            "QPushButton {"
            " background: #24282d;"
            " color: #eef1f4;"
            " border: 1px solid #464d55;"
            " border-radius: 7px;"
            " padding: 5px 10px;"
            "}"
            "QToolButton {"
            " background: #24282d;"
            " color: #eef1f4;"
            " border: 1px solid #464d55;"
            " border-radius: 7px;"
            " padding: 5px 10px;"
            "}"
            "QPushButton:hover, QToolButton:hover { background: #2e343a; border-color: #656d76; }"
            "QPushButton:pressed, QToolButton:pressed { background: #0f1113; }"
            "QPlainTextEdit {"
            " background: #0b0d0f;"
            " color: #eef1f4;"
            " border: 1px solid #4a5159;"
            " border-radius: 8px;"
            " padding: 5px 8px 14px 8px;"
            " selection-background-color: #c7ccd2;"
            " selection-color: #101214;"
            "}"
        )

        top_row = QHBoxLayout()
        top_row.setSpacing(7)
        self.chain_combo = QComboBox(parent)
        self.chain_combo.setMinimumWidth(170)
        self.chain_combo.currentIndexChanged.connect(self._chain_changed)
        top_row.addWidget(self.chain_combo, 0)

        self.selection_button = QToolButton(parent)
        self.selection_button.setText("Select: Residue")
        self.selection_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.selection_button.setMenu(self._build_selection_menu(parent))
        top_row.addWidget(self.selection_button, 0)

        self.status_label = QLabel("No protein sequence loaded.", parent)
        self.status_label.setMinimumWidth(0)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        top_row.addWidget(self.status_label, 1)

        self.refresh_button = QPushButton("Refresh", parent)
        self.refresh_button.clicked.connect(self.refresh)
        top_row.addWidget(self.refresh_button, 0)

        self.similar_button = QPushButton("Similar", parent)
        self.similar_button.clicked.connect(self._open_similar)
        top_row.addWidget(self.similar_button, 0)
        layout.addLayout(top_row)

        self.sequence_text = ClickableSequenceText(
            self._select_residue_range_by_index,
            self._show_residue_context_menu,
            parent,
        )
        self.sequence_text.setFixedHeight(70)
        layout.addWidget(self.sequence_text)

        self._attach_to_graphics_view()
        self.install_handlers()
        self.refresh()

    def displayed(self):
        return bool(self.bar_widget is not None and self.bar_widget.isVisible())

    def display(self, b):
        if b:
            self._attach_to_graphics_view()
            if self.bar_widget is not None:
                self.bar_widget.show()
        elif self.bar_widget is not None:
            self.bar_widget.hide()

    def _attach_to_graphics_view(self):
        mw = getattr(self.session.ui, "main_window", None)
        if mw is None or self.bar_widget is None:
            return
        current = mw.main_view
        if getattr(current, "_codex_sequence_wrapper", False):
            wrapper = current
            self._wrapper = wrapper
            self._previous_main_view = getattr(wrapper, "_codex_previous_main_view", None)
            layout = wrapper.layout()
            if self.bar_widget.parent() is not wrapper:
                layout.insertWidget(0, self.bar_widget)
            return

        wrapper = QWidget(mw)
        wrapper._codex_sequence_wrapper = True
        wrapper._codex_previous_main_view = current
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        wrapper.setLayout(layout)
        layout.addWidget(self.bar_widget)
        layout.addWidget(current, 1)
        self._wrapper = wrapper
        self._previous_main_view = current
        mw.main_view = wrapper

    def _detach_from_graphics_view(self):
        mw = getattr(self.session.ui, "main_window", None)
        wrapper = self._wrapper
        if self.bar_widget is not None:
            if wrapper is not None and wrapper.layout() is not None:
                try:
                    wrapper.layout().removeWidget(self.bar_widget)
                except Exception:
                    pass
            self.bar_widget.setParent(None)
        previous = self._previous_main_view
        self._wrapper = None
        self._previous_main_view = None
        if mw is not None and wrapper is not None and mw.main_view is wrapper and previous is not None:
            try:
                mw.main_view = previous
            except Exception:
                pass
        if wrapper is not None:
            try:
                wrapper.deleteLater()
            except Exception:
                pass

    def install_handlers(self):
        if self.handlers:
            return
        self.handlers = [
            self.session.triggers.add_handler("command finished", self._queue_refresh),
            self.session.triggers.add_handler("selection changed", self._queue_refresh),
        ]

    def delete(self):
        for handler in self.handlers:
            try:
                self.session.triggers.remove_handler(handler)
            except Exception:
                pass
        self.handlers = []
        self._detach_from_graphics_view()
        super().delete()

    def refresh(self):
        previous_spec = self._current_entry.get("spec") if self._current_entry else None
        self._entries = _protein_sequence_entries(self.session)
        self.chain_combo.blockSignals(True)
        self.chain_combo.clear()
        for entry in self._entries:
            self.chain_combo.addItem(entry["display"], entry["spec"])
        if self._entries:
            index = 0
            if previous_spec:
                found = self.chain_combo.findData(previous_spec)
                if found >= 0:
                    index = found
            self.chain_combo.setCurrentIndex(index)
        self.chain_combo.blockSignals(False)
        self._set_current_entry_from_combo()

    def _queue_refresh(self, *_args):
        if self._refresh_pending:
            return
        self._refresh_pending = True

        def run_refresh():
            self._refresh_pending = False
            self.refresh()

        QTimer.singleShot(150, run_refresh)

    def _chain_changed(self, _index):
        self._selected_ranges = []
        self._set_current_entry_from_combo()

    def _set_current_entry_from_combo(self):
        spec = self.chain_combo.currentData()
        self._current_entry = next((entry for entry in self._entries if entry["spec"] == spec), None)
        self._render_sequence()

    def _build_selection_menu(self, parent):
        menu = QMenu("Selection", parent)
        self._populate_selection_menu(menu)
        return menu

    def _populate_selection_menu(self, menu, residue_index=None):
        menu.clear()
        mode_menu = menu.addMenu("Click mode")
        for mode, label in (
            ("residue", "Residue / range"),
            ("helix", "Alpha helix segment"),
            ("strand", "Beta sheet segment"),
        ):
            action = mode_menu.addAction(label, lambda checked=False, m=mode: self._set_selection_click_mode(m))
            action.setCheckable(True)
            action.setChecked(self._selection_click_mode == mode)
        menu.addSeparator()
        menu.addAction("Select current chain", self._select_current_chain)
        menu.addAction("Clear selection", lambda: self._run_commands("Selection clear", ["select clear"]))
        menu.addSeparator()
        menu.addAction("Select alpha helices in chain", lambda: self._select_secondary_structure("helix", "chain"))
        if residue_index is not None:
            menu.addAction(
                "Select alpha helix segment at residue",
                lambda idx=residue_index: self._select_secondary_structure("helix", "segment", index=idx),
            )
        menu.addSeparator()
        menu.addAction("Select beta sheets in chain", lambda: self._select_secondary_structure("strand", "chain"))
        if residue_index is not None:
            menu.addAction(
                "Select beta sheet segment at residue",
                lambda idx=residue_index: self._select_secondary_structure("strand", "segment", index=idx),
            )
        menu.addSeparator()
        menu.addAction("FoldDisco from selected residues", self._open_folddisco)

    def _set_selection_click_mode(self, mode):
        if mode not in ("residue", "helix", "strand"):
            mode = "residue"
        self._selection_click_mode = mode
        label = {
            "residue": "Residue",
            "helix": "Alpha Helix",
            "strand": "Beta Sheet",
        }[mode]
        self.selection_button.setText(f"Select: {label}")
        action_text = {
            "residue": "Click/drag selects residues or ranges.",
            "helix": "Click selects the alpha helix segment containing that residue.",
            "strand": "Click selects the beta sheet segment containing that residue.",
        }[mode]
        self.status_label.setText(action_text)

    def _render_sequence(self):
        entry = self._current_entry
        if not entry:
            self.status_label.setText("No protein chain resolved. Open/select a protein model.")
            self.sequence_text.set_sequence_text([], 0, [])
            self._selected_ranges = []
            return

        motifs = _motif_hits_for_entry(self.session, entry)
        top_motifs = sorted(motifs, key=lambda hit: (hit.get("priority", 99), hit.get("start_number", 0), hit.get("pattern_name", "")))
        motif_bits = [
            f"{hit['pattern_name']} {hit['chain_id']}:{hit['start_number']}-{hit['end_number']}"
            for hit in top_motifs[:3]
        ]
        if len(motifs) > 3:
            motif_bits.append(f"+{len(motifs) - 3}")
        motif_text = ", ".join(motif_bits) if motif_bits else "no motifs"
        self.status_label.setText(
            f"{entry['spec']} · {entry['length']} residues · click/drag adds selection · right-click menu · {motif_text}"
        )
        lines = [
            _tick_line(entry["length"]),
            entry["sequence"],
        ]
        motif_line = _motif_line(entry, motifs)
        if motif_line:
            lines.append(motif_line)
        self.sequence_text.set_sequence_text(lines, entry["length"], entry["tooltips"])
        selected_ranges = self._selected_ranges_from_session(entry)
        self._selected_ranges = selected_ranges
        self.sequence_text.highlight_ranges(selected_ranges)
        if selected_ranges:
            self.status_label.setText(self._status_for_ranges(entry, selected_ranges))

    def _select_residue_range_by_index(self, start, end=None):
        entry = self._current_entry
        if end is None:
            end = start
        if not entry or not entry["residues"]:
            return
        start = max(0, min(int(start), len(entry["residues"]) - 1))
        end = max(0, min(int(end), len(entry["residues"]) - 1))
        if end < start:
            start, end = end, start
        if self._selection_click_mode in ("helix", "strand"):
            self._select_secondary_segments_by_range(entry, start, end, self._selection_click_mode)
            return
        spec = self._selection_spec_for_range(entry, start, end)
        if not spec:
            return
        try:
            from chimerax.core.commands import run

            with command_batch(self.session, f"Sequence selection {spec}"):
                run(self.session, f"select add {spec}")
        except Exception as err:
            message = str(err) if str(err) else err.__class__.__name__
            self.session.logger.error(f"Sequence selection failed: {message}")
            return
        self._selected_ranges = _merge_index_ranges(self._selected_ranges + [(start, end)])
        self.sequence_text.highlight_ranges(self._selected_ranges)
        self.status_label.setText(self._status_for_ranges(entry, self._selected_ranges, added=(start, end)))

    def _selection_spec_for_range(self, entry, start, end):
        residues = entry.get("residues") or []
        if start == end:
            return residues[start].get("spec")
        start_number = residues[start].get("number")
        end_number = residues[end].get("number")
        if start_number is None or end_number is None:
            return None
        return f"{entry['spec']}:{start_number}-{end_number}"

    def _select_secondary_segments_by_range(self, entry, start, end, selector):
        segments = self._secondary_segments_for_range(entry, start, end, selector)
        label = "beta sheet" if selector == "strand" else "alpha helix"
        if not segments:
            target = self._short_range_label(entry, start, end)
            self.status_label.setText(f"{target} does not touch a {label}.")
            return

        commands = []
        ranges = []
        for seg_start, seg_end in segments:
            segment_spec = self._selection_spec_for_range(entry, seg_start, seg_end)
            if segment_spec:
                commands.append(f"select add {segment_spec}")
            ranges.append((seg_start, seg_end))
        if not commands:
            self.status_label.setText(f"No selectable {label} segment found.")
            return
        try:
            from chimerax.core.commands import run

            with command_batch(self.session, f"Sequence {label} segment selection"):
                for command in commands:
                    run(self.session, command)
        except Exception as err:
            message = str(err) if str(err) else err.__class__.__name__
            self.session.logger.error(f"Sequence {label} selection failed: {message}")
            return

        self._selected_ranges = _merge_index_ranges(self._selected_ranges + ranges)
        self.sequence_text.highlight_ranges(self._selected_ranges)
        if len(ranges) == 1:
            self.status_label.setText(f"Added {label} {self._short_range_label(entry, ranges[0][0], ranges[0][1])}.")
        else:
            self.status_label.setText(f"Added {len(ranges)} {label} segments.")

    def _secondary_segments_for_range(self, entry, start, end, selector):
        segments = []
        seen = set()
        for index in range(start, end + 1):
            segment = self._secondary_segment_for_index(entry, index, selector)
            if segment is None:
                continue
            if segment in seen:
                continue
            seen.add(segment)
            segments.append(segment)
        return _merge_index_ranges(segments)

    def _status_for_range(self, entry, start, end):
        residues = entry.get("residues") or []
        spec = self._selection_spec_for_range(entry, start, end)
        if start == end:
            residue = residues[start]
            return f"Selected {residue['label']} · {spec}"
        first = residues[start]
        last = residues[end]
        return f"Selected {first['chain_id'] if 'chain_id' in first else entry['chain_id']}{first['number']}-{last['number']} · {spec}"

    def _status_for_ranges(self, entry, ranges, added=None):
        ranges = _merge_index_ranges(ranges)
        total = sum(end - start + 1 for start, end in ranges)
        if len(ranges) == 1:
            status = self._status_for_range(entry, ranges[0][0], ranges[0][1])
        else:
            preview = ", ".join(self._short_range_label(entry, start, end) for start, end in ranges[:5])
            if len(ranges) > 5:
                preview += f", +{len(ranges) - 5}"
            status = f"Selected {total} residues in {len(ranges)} blocks · {preview}"
        if added is not None:
            added_label = self._short_range_label(entry, added[0], added[1])
            status = f"Added {added_label} · {status}"
        return status

    def _short_range_label(self, entry, start, end):
        residues = entry.get("residues") or []
        if not residues:
            return ""
        start = max(0, min(int(start), len(residues) - 1))
        end = max(0, min(int(end), len(residues) - 1))
        if end < start:
            start, end = end, start
        chain_id = entry.get("chain_id", "?")
        first = residues[start]
        last = residues[end]
        if start == end:
            return f"{chain_id}{first['number']}"
        return f"{chain_id}{first['number']}-{last['number']}"

    def _selected_ranges_from_session(self, entry):
        try:
            from chimerax.atomic import selected_residues

            selected = selected_residues(self.session)
        except Exception:
            return []
        entry_model = str(entry.get("model_spec", ""))
        entry_chain = str(entry.get("chain_id", "") or "?")
        selected_numbers = set()
        try:
            chain_groups = selected.by_chain
        except Exception:
            chain_groups = []
        for structure, chain_id, residues in chain_groups:
            model_spec = f"#{getattr(structure, 'id_string', '?')}"
            chain_label = str(chain_id or "?").strip() or "?"
            if model_spec != entry_model or chain_label != entry_chain:
                continue
            try:
                selected_numbers.update(str(number) for number in residues.numbers)
            except Exception:
                continue
        if not selected_numbers:
            return []
        indices = [
            index
            for index, residue in enumerate(entry.get("residues") or [])
            if str(residue.get("number")) in selected_numbers
        ]
        if not indices:
            return []
        ranges = []
        start = previous = indices[0]
        for index in indices[1:]:
            if index == previous + 1:
                previous = index
                continue
            ranges.append((start, previous))
            start = previous = index
        ranges.append((start, previous))
        return ranges

    def _show_residue_context_menu(self, index, global_pos):
        entry = self._current_entry
        if not entry or index < 0 or index >= len(entry["residues"]):
            return
        residue = entry["residues"][index]
        residue_spec = residue.get("spec")
        if not residue_spec:
            return
        chain_spec = entry["spec"]
        model_spec = entry["model_spec"]
        label = residue["label"]

        menu = QMenu(self.session.ui.main_window)
        action_menu = menu.addMenu("Action")
        action_menu.addAction(
            "Select residue",
            lambda: self._run_commands(f"Context residue:{residue_spec}", [f"select {residue_spec}"]),
        )
        action_menu.addAction(
            "Select chain",
            lambda: self._run_commands(f"Context chain:{chain_spec}", [f"select {chain_spec}"]),
        )
        action_menu.addSeparator()
        action_menu.addAction(
            "Focus residue",
            lambda: self._run_commands(f"Context focus residue:{residue_spec}", [f"select {residue_spec}", "view sel"]),
        )
        action_menu.addAction(
            "Focus chain",
            lambda: self._run_commands(f"Context focus chain:{chain_spec}", [f"select {chain_spec}", "view sel"]),
        )
        action_menu.addSeparator()
        action_menu.addAction(
            "FoldDisco this residue",
            lambda spec=residue_spec: self._open_folddisco(residue_spec=spec),
        )

        selection_menu = menu.addMenu("Selection")
        self._populate_selection_menu(selection_menu, residue_index=index)

        motif_hits = self._motifs_containing_residue(entry, residue)
        if motif_hits:
            motif_menu = menu.addMenu("Motif")
            for hit in motif_hits[:6]:
                motif_label = f"{hit['pattern_name']} {hit['start_number']}-{hit['end_number']}"
                residue_specs = list(hit.get("residue_specs") or [])
                motif_menu.addAction(
                    "Select " + motif_label,
                    lambda specs=residue_specs, label=motif_label: self._select_motif_specs(specs, label),
                )
                motif_menu.addAction(
                    "FoldDisco " + motif_label,
                    lambda specs=residue_specs, label=motif_label: self._open_folddisco(residue_specs=specs, label=label),
                )
            motif_menu.addSeparator()
            motif_menu.addAction("Motif report", lambda: self._launch_ai_prompt("/motif", "analyze"))

        show_menu = menu.addMenu("Show")
        show_menu.addAction("Sticks", lambda: self._show_sticks(residue_spec))
        show_menu.addAction("Surface", lambda: self._run_commands(f"Context surface:{residue_spec}", [f"surface {residue_spec}"]))
        show_menu.addAction("Chain surface", lambda: self._run_commands(f"Context chain surface:{chain_spec}", [f"surface {chain_spec}"]))
        show_menu.addAction("Cartoon chain", lambda: self._run_commands(f"Context cartoon:{chain_spec}", [f"cartoon {chain_spec}"]))

        hide_menu = menu.addMenu("Hide")
        hide_menu.addAction("Atoms", lambda: self._run_commands(f"Context hide atoms:{residue_spec}", [f"hide {residue_spec} atoms"]))
        hide_menu.addAction("Surface", lambda: self._run_commands(f"Context hide surface:{residue_spec}", [f"~surface {residue_spec}"]))
        hide_menu.addAction("Chain surface", lambda: self._run_commands(f"Context hide chain surface:{chain_spec}", [f"~surface {chain_spec}"]))
        hide_menu.addAction("Cartoon chain", lambda: self._run_commands(f"Context hide cartoon:{chain_spec}", [f"hide {chain_spec} cartoons"]))

        color_menu = menu.addMenu("Color")
        color_menu.addAction("Carbon context + hetero elements", lambda: self._apply_context_stick_colors(residue_spec))
        color_menu.addAction("By element", lambda: self._run_commands(f"Context color byelement:{residue_spec}", [f"color {residue_spec} byelement"]))
        color_menu.addAction("By chain", lambda: self._run_commands(f"Context color bychain:{chain_spec}", [f"color {chain_spec} bychain"]))
        color_menu.addAction("By model", lambda: self._run_commands(f"Context color bymodel:{model_spec}", [f"color {model_spec} bymodel"]))
        preset_menu = color_menu.addMenu("Preset")
        for color_name in ("yellow", "cyan", "magenta", "hotpink", "cornflowerblue", "orange", "gold"):
            preset_menu.addAction(
                color_name,
                lambda checked=False, c=color_name: self._run_commands(
                    f"Context color {c}:{residue_spec}",
                    [f"color {residue_spec} {c}"],
                ),
            )
        color_menu.addAction("Custom...", lambda: self._pick_custom_color(residue_spec))

        label_menu = menu.addMenu("Label")
        label_menu.addAction("Residue", lambda: self._run_commands(f"Context label:{residue_spec}", [f"label {residue_spec} residues"]))
        label_menu.addAction("Chain", lambda: self._run_commands(f"Context chain label:{chain_spec}", [f"label {chain_spec} residues"]))
        label_menu.addAction("Clear labels", lambda: self._run_commands("Context label clear", ["label delete"]))

        ai_menu = menu.addMenu("AI")
        ai_menu.addAction("Analyze residue", lambda: self._launch_ai_prompt(f"Analyze {residue_spec} with evidence and confidence.", "analyze"))
        ai_menu.addAction("Improve chain view", lambda: self._launch_ai_prompt(f"Improve the view for {chain_spec} and apply the changes directly.", "agent"))

        self.status_label.setText(f"Menu target: {label} · {residue_spec}")
        self.session.ui.post_context_menu(menu, global_pos)

    def _run_commands(self, batch_label, commands):
        from chimerax.core.commands import run

        with command_batch(self.session, batch_label):
            for command in commands:
                run(self.session, command)
        self.session.logger.status(batch_label)

    def _motifs_containing_residue(self, entry, residue):
        try:
            number = int(residue.get("number"))
        except Exception:
            return []
        hits = []
        for hit in _motif_hits_for_entry(self.session, entry):
            try:
                start = int(hit.get("start_number", 0))
                end = int(hit.get("end_number", 0))
            except Exception:
                continue
            if start <= number <= end:
                hits.append(hit)
        hits.sort(key=lambda item: (item.get("priority", 99), item.get("start_number", 0), item.get("pattern_name", "")))
        return hits

    def _select_motif_specs(self, residue_specs, label):
        specs = [str(spec) for spec in residue_specs if str(spec or "").strip()]
        if not specs:
            self.status_label.setText("No motif residue specs available.")
            return
        self._run_commands(f"Select motif:{label}", ["select " + " ".join(specs)])
        self.status_label.setText(f"Selected motif {label}.")

    def _select_current_chain(self):
        entry = self._current_entry
        if not entry:
            self.status_label.setText("No protein chain resolved.")
            return
        self._run_commands(f"Select chain {entry['spec']}", [f"select {entry['spec']}"])
        self.status_label.setText(f"Selected chain {entry['spec']}.")

    def _select_secondary_structure(self, selector, scope, index=None):
        entry = self._current_entry
        if not entry:
            self.status_label.setText("No protein chain resolved.")
            return
        selector = "strand" if selector == "strand" else "helix"
        label = "beta sheet" if selector == "strand" else "alpha helix"
        chain_spec = entry["spec"]
        if scope == "segment":
            segment = self._secondary_segment_for_index(entry, index, selector)
            if segment is None:
                residue = entry["residues"][index] if index is not None and 0 <= index < len(entry["residues"]) else None
                target = residue["label"] if residue else "that residue"
                self.status_label.setText(f"{target} is not in a {label}.")
                return
            start, end = segment
            segment_spec = self._selection_spec_for_range(entry, start, end)
            if not segment_spec:
                self.status_label.setText(f"No selectable {label} segment found.")
                return
            command = f"select {segment_spec}"
            scope_label = f"segment {self._short_range_label(entry, start, end)}"
        else:
            command = f"select {chain_spec} & {selector}"
            scope_label = "in this chain"
        self._run_commands(f"Select {label} {scope_label}", [command])
        self.status_label.setText(f"Selected {label} {scope_label}.")

    def _secondary_segment_for_index(self, entry, index, selector):
        residues = entry.get("residues") or []
        if index is None or index < 0 or index >= len(residues):
            return None
        if not _residue_matches_secondary(residues[index], selector):
            return None
        ss_id = _safe_int(residues[index].get("ss_id"))
        start = end = index
        while start > 0 and _residue_matches_secondary(residues[start - 1], selector, ss_id):
            start -= 1
        last = len(residues) - 1
        while end < last and _residue_matches_secondary(residues[end + 1], selector, ss_id):
            end += 1
        return (start, end)

    def _show_sticks(self, residue_spec):
        from chimerax.core.commands import run
        from .display_color import apply_stick_context_colors

        with command_batch(self.session, f"Context sticks:{residue_spec}"):
            run(self.session, f"show {residue_spec} atoms")
            run(self.session, f"style {residue_spec} stick")
            apply_stick_context_colors(self.session, residue_spec)
        self.session.logger.status(f"Context sticks:{residue_spec}")

    def _apply_context_stick_colors(self, residue_spec):
        from .display_color import apply_stick_context_colors

        with command_batch(self.session, f"Context stick colors:{residue_spec}"):
            apply_stick_context_colors(self.session, residue_spec)
        self.session.logger.status(f"Context stick colors:{residue_spec}")

    def _pick_custom_color(self, spec):
        from Qt.QtGui import QColor
        from Qt.QtWidgets import QColorDialog

        color = QColorDialog.getColor(QColor("#ffd166"), self.session.ui.main_window, "Choose ChimeraX Color")
        if not color.isValid():
            return
        self._run_commands(f"Context color custom:{spec}", [f"color {spec} {color.name()}"])

    def _launch_ai_prompt(self, prompt, mode):
        from .tool import CodexAssistant

        assistant = CodexAssistant.get_singleton(self.session)
        if assistant is not None:
            assistant.display(True)
            assistant._launch_quick_request(prompt, mode)

    def _open_similar(self):
        entry = self._current_entry
        if not entry:
            self.status_label.setText("No protein sequence loaded for Similar.")
            return

        self.status_label.setText(f"Opening RCSB Similar for {entry['spec']}...")

        def worker():
            try:
                from .toolbar_actions import launch_sequence_analysis_site

                payload = [
                    {
                        "spec": entry["spec"],
                        "structure_name": entry["model_name"],
                        "chain_id": entry["chain_id"],
                        "sequence": entry["sequence"],
                    }
                ]
                message = launch_sequence_analysis_site(self.session, "rcsb", entries=payload)
            except Exception as err:
                message = f"Similar search failed: {str(err) if str(err) else err.__class__.__name__}"
                error = True
            else:
                error = False

            def finish():
                if error:
                    self.session.logger.error(message)
                else:
                    self.session.logger.info(message)
                self.status_label.setText(message)

            self.session.ui.thread_safe(finish)

        threading.Thread(target=worker, daemon=True).start()

    def _open_folddisco(self, residue_spec=None, residue_specs=None, label=None):
        specs = []
        if residue_specs:
            specs.extend(str(spec) for spec in residue_specs if str(spec or "").strip())
        elif residue_spec:
            specs.append(str(residue_spec))
        if specs:
            try:
                from chimerax.core.commands import run

                batch_label = f"FoldDisco motif:{label}" if label else "FoldDisco residue"
                with command_batch(self.session, batch_label):
                    run(self.session, "select " + " ".join(specs))
            except Exception as err:
                message = str(err) if str(err) else err.__class__.__name__
                self.session.logger.error(f"FoldDisco selection failed: {message}")
                return
        try:
            from .toolbar_actions import launch_folddisco

            message = launch_folddisco(self.session)
        except Exception as err:
            message = f"FoldDisco failed: {str(err) if str(err) else err.__class__.__name__}"
            self.session.logger.error(message)
            return
        self.session.logger.info(message)
        self.status_label.setText(message)


def _protein_sequence_entries(session):
    from chimerax.atomic import AtomicStructure, Residue

    entries = []
    for model in session.models.list(type=AtomicStructure):
        model_spec = f"#{getattr(model, 'id_string', '?')}"
        model_name = getattr(model, "name", "structure")
        for chain in getattr(model, "chains", []):
            polymer_type = getattr(chain, "polymer_type", None)
            if polymer_type not in (Residue.PT_AMINO, Residue.PT_PROTEIN):
                continue
            entry = _entry_for_chain(model_spec, model_name, chain)
            if entry:
                entries.append(entry)
    entries.sort(key=lambda item: (item["model_spec"], item["chain_id"]))
    return entries


def _entry_for_chain(model_spec, model_name, chain):
    residues = getattr(chain, "existing_residues", None)
    if residues is None or len(residues) == 0:
        return None

    chain_id = str(getattr(chain, "chain_id", "") or "?").strip() or "?"
    names = [str(name).upper() for name in _safe_list(getattr(residues, "names", []))]
    numbers = [str(number) for number in _safe_list(getattr(residues, "numbers", []))]
    residue_objects = _safe_residue_list(residues)
    length = min(len(names), len(numbers))
    if length <= 0:
        return None
    names = names[:length]
    numbers = numbers[:length]
    residue_objects = residue_objects[:length] if residue_objects else []
    helix_flags = _safe_list(getattr(residues, "is_helix", []))
    strand_flags = _safe_list(getattr(residues, "is_strand", []))
    ss_ids = _safe_list(getattr(residues, "ss_ids", []))
    ss_types = _safe_list(getattr(residues, "ss_types", []))

    sequence = "".join(AA3_TO_1.get(name, "X") for name in names)
    residues_payload = []
    tooltips = []
    for index in range(length):
        residue = residue_objects[index] if index < len(residue_objects) else None
        spec = getattr(residue, "atomspec", "") if residue is not None else ""
        if not spec:
            spec = f"{model_spec}/{chain_id}:{numbers[index]}"
        label = f"{chain_id}{numbers[index]} {names[index]} ({sequence[index]})"
        ss_id = getattr(residue, "ss_id", ss_ids[index] if index < len(ss_ids) else 0)
        ss_type = getattr(residue, "ss_type", ss_types[index] if index < len(ss_types) else 0)
        is_helix = getattr(residue, "is_helix", helix_flags[index] if index < len(helix_flags) else False)
        is_strand = getattr(residue, "is_strand", strand_flags[index] if index < len(strand_flags) else False)
        is_helix = bool(is_helix) or _safe_int(ss_type) == 1
        is_strand = bool(is_strand) or _safe_int(ss_type) == 2
        residues_payload.append(
            {
                "number": numbers[index],
                "name": names[index],
                "letter": sequence[index],
                "label": label,
                "spec": spec,
                "is_helix": is_helix,
                "is_strand": is_strand,
                "ss_id": _safe_int(ss_id),
                "ss_type": _safe_int(ss_type),
            }
        )
        tooltips.append(f"{label} · click or drag to select")

    return {
        "model_spec": model_spec,
        "model_name": model_name,
        "chain_id": chain_id,
        "spec": f"{model_spec}/{chain_id}",
        "display": f"{model_spec}/{chain_id} · {model_name}",
        "sequence": sequence,
        "length": length,
        "residues": residues_payload,
        "tooltips": tooltips,
    }


def _safe_residue_list(residues):
    try:
        return list(residues)
    except Exception:
        return []


def _safe_list(values):
    try:
        return list(values)
    except Exception:
        return []


def _merge_index_ranges(ranges):
    cleaned = []
    for start, end in ranges or []:
        start = int(start)
        end = int(end)
        if end < start:
            start, end = end, start
        cleaned.append((start, end))
    if not cleaned:
        return []

    cleaned.sort(key=lambda item: (item[0], item[1]))
    merged = [cleaned[0]]
    for start, end in cleaned[1:]:
        previous_start, previous_end = merged[-1]
        if start <= previous_end + 1:
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _residue_matches_secondary(residue, selector, ss_id=None):
    selector = "strand" if selector == "strand" else "helix"
    if selector == "strand":
        matches = bool(residue.get("is_strand")) or _safe_int(residue.get("ss_type")) == 2
    else:
        matches = bool(residue.get("is_helix")) or _safe_int(residue.get("ss_type")) == 1
    if not matches:
        return False
    if ss_id is None or ss_id <= 0:
        return True
    return _safe_int(residue.get("ss_id")) == ss_id


def _motif_hits_for_entry(session, entry):
    try:
        from .semantic import get_motif_hits

        return [
            hit for hit in get_motif_hits(session, model_hint=entry["model_spec"])
            if str(hit.get("chain_id", "")) == entry["chain_id"]
        ]
    except Exception:
        return []


def _tick_line(length):
    chars = [" "] * int(length or 0)
    for pos in range(10, len(chars) + 1, 10):
        label = str(pos)
        start = max(0, pos - len(label))
        for offset, char in enumerate(label):
            target = start + offset
            if target < len(chars):
                chars[target] = char
    return "".join(chars)


def _motif_line(entry, motifs):
    length = entry["length"]
    chars = [" "] * length
    indexed_numbers = []
    for residue in entry["residues"]:
        try:
            indexed_numbers.append(int(residue["number"]))
        except Exception:
            indexed_numbers.append(None)
    for hit in motifs:
        try:
            start = int(hit.get("start_number", 0))
            end = int(hit.get("end_number", 0))
        except Exception:
            continue
        for index, number in enumerate(indexed_numbers):
            if number is not None and start <= number <= end:
                chars[index] = "^"
    text = "".join(chars).rstrip()
    return text if text else ""
