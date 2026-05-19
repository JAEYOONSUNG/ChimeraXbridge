import threading

from Qt.QtCore import Qt, QTimer
from Qt.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QKeySequence,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
)
from Qt.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollBar,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from chimerax.core.tools import ToolInstance, get_singleton

from .display_color import restore_charge_colors
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


METAL_ELEMENT_SYMBOLS = {
    "LI", "NA", "K", "RB", "CS",
    "BE", "MG", "CA", "SR", "BA",
    "AL", "GA", "IN", "TL",
    "SC", "Y", "LA", "CE", "PR", "ND", "PM", "SM", "EU", "GD",
    "TB", "DY", "HO", "ER", "TM", "YB", "LU",
    "TI", "ZR", "HF", "V", "NB", "TA", "CR", "MO", "W",
    "MN", "TC", "RE", "FE", "RU", "OS", "CO", "RH", "IR",
    "NI", "PD", "PT", "CU", "AG", "AU", "ZN", "CD", "HG",
    "SN", "PB", "BI", "U",
}

METAL_COORDINATION_CUTOFFS = {
    "LI": 2.8,
    "NA": 3.2,
    "K": 4.0,
    "MG": 2.8,
    "CA": 3.6,
    "MN": 3.1,
    "FE": 3.0,
    "CO": 3.0,
    "NI": 3.0,
    "CU": 3.0,
    "ZN": 3.0,
    "CD": 3.2,
}

METAL_DONOR_ELEMENT_NUMBERS = {7, 8, 15, 16}


def _sequence_panel_font():
    font = QFont("Courier New")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setFixedPitch(True)
    font.setKerning(False)
    try:
        base = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        point_size = base.pointSize()
        if point_size > 0:
            font.setPointSize(point_size)
    except Exception:
        pass
    return font


class ClickableSequenceText(QPlainTextEdit):
    """A fixed-width sequence strip where every character maps to a residue."""

    def __init__(
        self,
        click_callback,
        context_callback,
        parent=None,
        clear_callback=None,
        metal_click_callback=None,
        metal_context_callback=None,
        metal_hover_callback=None,
    ):
        super().__init__(parent)
        self._click_callback = click_callback
        self._context_callback = context_callback
        self._clear_callback = clear_callback
        self._metal_click_callback = metal_click_callback
        self._metal_context_callback = metal_context_callback
        self._metal_hover_callback = metal_hover_callback
        self._sequence_length = 0
        self._line_starts = []
        self._tooltips = []
        self._metal_ranges = []
        self._drag_anchor = None
        self._drag_current = None
        self._drag_additive = False
        self._drag_shift_extend = False
        self._range_anchor = None
        self._highlight_ranges = []
        self._preview_range = None
        self._search_ranges = []
        self._search_active = -1
        self._residue_styles = []
        self._base_selection_cache = []
        self._base_selection_cache_key = None
        self._highlight_apply_pending = False
        self.setReadOnly(True)
        self.setMouseTracking(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setViewportMargins(0, 0, 0, 0)
        self.setFixedHeight(78)
        self.setFont(_sequence_panel_font())
        self.setPlaceholderText("Load a protein model to show a clickable sequence.")

    def set_sequence_text(self, lines, sequence_length, tooltips, residue_styles=None, metal_ranges=None):
        text = "\n".join(lines)
        starts = []
        position = 0
        for line in lines:
            starts.append(position)
            position += len(line) + 1
        text_changed = text != self.toPlainText()
        metal_ranges = list(metal_ranges or [])
        metal_ranges_changed = metal_ranges != self._metal_ranges
        self._sequence_length = int(sequence_length or 0)
        self._line_starts = starts
        self._tooltips = list(tooltips or [])
        self._metal_ranges = metal_ranges
        self._residue_styles = list(residue_styles or [])
        if metal_ranges_changed:
            self._base_selection_cache = []
            self._base_selection_cache_key = None
        if text_changed:
            self.setPlainText(text)
            self._search_ranges = []
            self._search_active = -1
            self._base_selection_cache = []
            self._base_selection_cache_key = None
        self._drag_anchor = None
        self._drag_current = None
        self._drag_additive = False
        self._drag_shift_extend = False
        self._range_anchor = None
        self._preview_range = None
        self._apply_highlights()

    def set_search_ranges(self, ranges, active=-1):
        self._search_ranges = self._normalize_ranges(ranges)
        if 0 <= int(active) < len(self._search_ranges):
            self._search_active = int(active)
        else:
            self._search_active = -1
        self._base_selection_cache = []
        self._base_selection_cache_key = None
        self._apply_highlights()

    def scroll_to_index(self, index):
        if not self._line_starts or self._sequence_length <= 0:
            return
        sequence_line_start = (
            self._line_starts[1] if len(self._line_starts) > 1 else self._line_starts[0]
        )
        target = max(0, min(int(index), self._sequence_length - 1))
        cursor = QTextCursor(self.document())
        cursor.setPosition(sequence_line_start + target)
        rect = self.cursorRect(cursor)
        bar = self.horizontalScrollBar()
        if bar is None:
            return
        viewport_width = max(1, self.viewport().width())
        absolute_x = rect.x() + bar.value()
        target_x = int(absolute_x - viewport_width // 2)
        target_x = max(bar.minimum(), min(bar.maximum(), target_x))
        bar.setValue(target_x)

    def mousePressEvent(self, event):
        context_click = (
            event.button() == Qt.MouseButton.RightButton
            or (
                event.button() == Qt.MouseButton.LeftButton
                and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            )
        )
        if context_click:
            metal = self._metal_at(event.pos())
            if metal is not None and self._metal_context_callback is not None:
                self._drag_anchor = None
                self._drag_current = None
                self._metal_context_callback(metal, self._event_global_pos(event))
                event.accept()
                return
            index = self._residue_index_at(event.pos())
            if index is not None:
                self.preview_range(index, index, immediate=True)
                self._drag_anchor = None
                self._drag_current = None
                self._context_callback(index, self._event_global_pos(event))
                event.accept()
                return
        if event.button() == Qt.MouseButton.LeftButton:
            metal = self._metal_at(event.pos())
            if metal is not None and self._metal_click_callback is not None:
                self._drag_anchor = None
                self._drag_current = None
                self._metal_click_callback(metal)
                event.accept()
                return
            index = self._residue_index_at(event.pos())
            if index is not None:
                # Clicks/drags inside the sequence text always accumulate.
                # Already-selected residues are toggled off downstream;
                # clearing the whole selection is done by clicking the
                # empty area outside the text.
                shift_extend = (
                    bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                    and self._range_anchor is not None
                )
                anchor = self._range_anchor if shift_extend else index
                self._drag_anchor = anchor
                self._drag_current = index
                self._drag_additive = True
                self._drag_shift_extend = shift_extend
                self.preview_range(anchor, index, immediate=True)
                event.accept()
                return
            if self._clear_callback is not None:
                self.preview_range(None, None, immediate=True)
                self._range_anchor = None
                self._clear_callback()
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
                if index == self._drag_current:
                    event.accept()
                    return
                self._drag_current = index
                self.preview_range(self._drag_anchor, self._drag_current)
            event.accept()
            return
        metal = self._metal_at(event.pos())
        if metal is not None:
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            tooltip = str(metal.get("tooltip") or metal.get("label") or metal.get("spec") or "")
            self.setToolTip(tooltip)
            if self._metal_hover_callback is not None:
                self._metal_hover_callback(metal)
            super().mouseMoveEvent(event)
            return
        if index is None:
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
            self.setToolTip("")
            if self._metal_hover_callback is not None:
                self._metal_hover_callback(None)
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
            additive = self._drag_additive
            shift_extend = self._drag_shift_extend
            self._drag_anchor = None
            self._drag_current = None
            self._drag_additive = False
            self._drag_shift_extend = False
            self.preview_range(None, None, immediate=True)
            if not shift_extend:
                self._range_anchor = start
            self._click_callback(start, end, additive)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if self._drag_anchor is None:
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
            self.setToolTip("")
            if self._metal_hover_callback is not None:
                self._metal_hover_callback(None)
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

    def _metal_at(self, pos):
        if not self._metal_ranges or len(self._line_starts) < 2:
            return None
        cursor = self.cursorForPosition(pos)
        col = cursor.position() - self._line_starts[1]
        for start, end, metal in self._metal_ranges:
            if start <= col < end:
                return metal
        return None

    def _highlight_residue(self, index):
        self.highlight_range(index, index)

    def highlight_range(self, start, end):
        self.highlight_ranges([(start, end)])

    def highlight_ranges(self, ranges):
        self._highlight_ranges = self._normalize_ranges(ranges)
        self._preview_range = None
        self._apply_highlights()

    def preview_range(self, start, end, *, immediate=False):
        if start is None or end is None:
            preview = None
        else:
            normalized = self._normalize_ranges([(start, end)])
            preview = normalized[0] if normalized else None
        if preview == self._preview_range:
            return
        self._preview_range = preview
        if immediate:
            self._apply_highlights()
        else:
            self._queue_highlight_apply()

    def _queue_highlight_apply(self):
        if getattr(self, "_highlight_apply_pending", False):
            return
        self._highlight_apply_pending = True

        def apply_later():
            self._highlight_apply_pending = False
            self._apply_highlights()

        QTimer.singleShot(16, apply_later)

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
        format_selected.setBackground(QColor("#005ce6"))
        format_selected.setForeground(QColor("#ffffff"))
        format_selected.setFontWeight(900)
        format_selected.setFontUnderline(True)
        format_preview = QTextCharFormat()
        format_preview.setBackground(QColor("#ffd23f"))
        format_preview.setForeground(QColor("#000000"))
        format_preview.setFontWeight(900)
        format_preview.setFontUnderline(True)
        format_search = QTextCharFormat()
        format_search.setBackground(QColor("#3d3417"))
        format_search.setForeground(QColor("#fce58e"))
        format_search_active = QTextCharFormat()
        format_search_active.setBackground(QColor("#f5c042"))
        format_search_active.setForeground(QColor("#000000"))
        format_search_active.setFontWeight(900)

        selections = list(self._base_highlight_selections(sequence_line_start, format_search, format_search_active))

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

    def _base_highlight_selections(self, sequence_line_start, format_search, format_search_active):
        key = (
            int(sequence_line_start),
            int(self._sequence_length),
            id(self._residue_styles),
            tuple((int(start), int(end), str((metal or {}).get("spec") or "")) for start, end, metal in self._metal_ranges),
            tuple(self._search_ranges),
            int(self._search_active),
        )
        if key == self._base_selection_cache_key:
            return self._base_selection_cache
        selections = []
        selections.extend(self._residue_style_selections(sequence_line_start))
        selections.extend(self._metal_token_selections(sequence_line_start))
        for index, (start, end) in enumerate(self._search_ranges):
            cursor = QTextCursor(self.document())
            cursor.setPosition(sequence_line_start + start)
            cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, end - start + 1)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = format_search_active if index == self._search_active else format_search
            selections.append(selection)
        self._base_selection_cache_key = key
        self._base_selection_cache = selections
        return selections

    def _metal_token_selections(self, sequence_line_start):
        selections = []
        if not self._metal_ranges:
            return selections
        for start, end, metal in self._metal_ranges:
            if end <= start:
                continue
            cursor = QTextCursor(self.document())
            cursor.setPosition(sequence_line_start + start)
            cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, end - start)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            fmt = QTextCharFormat()
            fmt.setForeground(QColor("#a8e6ff"))
            fmt.setBackground(QColor("#162631"))
            fmt.setFontWeight(900)
            fmt.setFontUnderline(True)
            selection.format = fmt
            selections.append(selection)
        return selections

    def _residue_style_selections(self, sequence_line_start):
        styles = self._residue_styles[: self._sequence_length]
        if not styles:
            return []
        selections = []
        run_start = None
        run_style = None
        for index in range(self._sequence_length):
            style = _sequence_text_style(styles[index] if index < len(styles) else None)
            if style is None:
                if run_start is not None:
                    selections.append(self._style_selection(sequence_line_start, run_start, index - 1, run_style))
                    run_start = None
                    run_style = None
                continue
            if run_style == style:
                continue
            if run_start is not None:
                selections.append(self._style_selection(sequence_line_start, run_start, index - 1, run_style))
            run_start = index
            run_style = style
        if run_start is not None:
            selections.append(self._style_selection(sequence_line_start, run_start, self._sequence_length - 1, run_style))
        return selections

    def _style_selection(self, sequence_line_start, start, end, style):
        cursor = QTextCursor(self.document())
        cursor.setPosition(sequence_line_start + start)
        cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, end - start + 1)
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        fmt = QTextCharFormat()
        fg = style.get("foreground")
        bg = style.get("background")
        weight = style.get("font_weight")
        underline = style.get("font_underline")
        if fg is not None:
            fmt.setForeground(fg)
        if bg is not None:
            fmt.setBackground(bg)
        if weight is not None:
            fmt.setFontWeight(int(weight))
        if underline is not None:
            fmt.setFontUnderline(bool(underline))
        selection.format = fmt
        return selection


class ClickableMetalText(QPlainTextEdit):
    """A compact PyMOL-like metal token strip independent of polymer numbering."""

    def __init__(self, click_callback, context_callback, hover_callback=None, parent=None):
        super().__init__(parent)
        self._click_callback = click_callback
        self._context_callback = context_callback
        self._hover_callback = hover_callback
        self._token_ranges = []
        self.setReadOnly(True)
        self.setMouseTracking(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setViewportMargins(0, 0, 0, 0)
        self.setFixedHeight(0)
        self.setFont(_sequence_panel_font())
        self.setVisible(False)

    def set_metals(self, metals, sequence_length=0):
        metals = list(metals or [])
        self._token_ranges = []
        if not metals:
            self.setPlainText("")
            self.setFixedHeight(0)
            self.setVisible(False)
            return
        prefix = "Metals  "
        text = prefix
        position = len(prefix)
        for index, metal in enumerate(metals):
            token = str(metal.get("token") or metal.get("element") or "M").strip()
            if index:
                text += "  "
                position += 2
            start = position
            text += token
            position += len(token)
            self._token_ranges.append((start, position, metal))
        if text != self.toPlainText():
            self.setPlainText(text)
        self.setFixedHeight(32)
        self.setVisible(True)

    def mousePressEvent(self, event):
        metal = self._metal_at(event.pos())
        if metal is not None and (
            event.button() == Qt.MouseButton.RightButton
            or (
                event.button() == Qt.MouseButton.LeftButton
                and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            )
        ):
            self._context_callback(metal, self._event_global_pos(event))
            event.accept()
            return
        if metal is not None and event.button() == Qt.MouseButton.LeftButton:
            self._click_callback(metal)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        metal = self._metal_at(event.pos())
        if metal is None:
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
            self.setToolTip("")
        else:
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            tooltip = str(metal.get("tooltip") or metal.get("label") or metal.get("spec") or "")
            self.setToolTip(tooltip)
            if self._hover_callback is not None:
                self._hover_callback(metal)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        self.setToolTip("")
        if self._hover_callback is not None:
            self._hover_callback(None)
        super().leaveEvent(event)

    def _metal_at(self, pos):
        cursor = self.cursorForPosition(pos)
        position = cursor.position()
        for start, end, metal in self._token_ranges:
            if start <= position < end:
                return metal
        return None

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


class ClickableAlignmentText(QPlainTextEdit):
    """A fixed-width alignment strip where columns can be selected."""

    def __init__(
        self,
        click_callback,
        hover_callback=None,
        parent=None,
        metal_click_callback=None,
        metal_context_callback=None,
        metal_hover_callback=None,
    ):
        super().__init__(parent)
        self._click_callback = click_callback
        self._hover_callback = hover_callback
        self._metal_click_callback = metal_click_callback
        self._metal_context_callback = metal_context_callback
        self._metal_hover_callback = metal_hover_callback
        self._alignment_length = 0
        self._line_starts = []
        self._drag_anchor = None
        self._drag_current = None
        self._drag_shift_extend = False
        self._range_anchor_by_row = {}
        self._alignment_styles = {}
        self._alignment_metal_ranges = []
        self._alignment_offset = 0
        self._alignment_row_lines = {"reference": 1, "moving": 3}
        self._alignment_sequence_rows = ["reference", "moving"]
        self._alignment_tooltips = []
        self._hover_column = None
        self._preview_range = None
        self._preview_row = None
        self._selected_ranges = {"reference": [], "moving": []}
        self._search_ranges = []
        self._search_active = -1
        self._style_selection_cache = []
        self._style_selection_cache_key = None
        self._alignment_apply_pending = False
        self.setReadOnly(True)
        self.setMouseTracking(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFont(_sequence_panel_font())

    def set_alignment_text(self, text, alignment_length, alignment_styles=None, layout=None, tooltips=None):
        lines = str(text or "").splitlines()
        starts = []
        position = 0
        for line in lines:
            starts.append(position)
            position += len(line) + 1
        self._alignment_length = int(alignment_length or 0)
        self._line_starts = starts
        self._drag_anchor = None
        self._drag_current = None
        self._drag_shift_extend = False
        self._hover_column = None
        self._preview_range = None
        self._preview_row = None
        self._selected_ranges = {"reference": [], "moving": []}
        self._search_ranges = []
        self._search_active = -1
        self._range_anchor_by_row = {}
        self._alignment_styles = dict(alignment_styles or {})
        self._style_selection_cache = []
        self._style_selection_cache_key = None
        layout = dict(layout or {})
        self._alignment_metal_ranges = list(layout.get("metal_ranges") or [])
        self._alignment_offset = max(0, int(layout.get("offset", 0) or 0))
        self._alignment_row_lines = dict(layout.get("row_lines") or {"reference": 1, "moving": 3})
        sequence_rows = list(layout.get("sequence_rows") or [])
        if not sequence_rows:
            sequence_rows = [
                row_name
                for row_name in self._alignment_row_lines
                if row_name not in ("ruler", "match", "consensus")
            ]
        self._alignment_sequence_rows = sequence_rows or ["reference", "moving"]
        self._alignment_tooltips = list(tooltips or [])
        self.setPlainText(text)
        self._apply_alignment_styles()

    def highlight_columns(self, selected_ranges):
        normalized = {}
        row_names = list(self._alignment_sequence_rows or ["reference", "moving"])
        for row_name in row_names:
            normalized[row_name] = self._normalize_column_ranges(
                (selected_ranges or {}).get(row_name)
            )
        self._selected_ranges = normalized
        self._apply_alignment_styles()

    def set_search_ranges(self, ranges, active=-1):
        row_names = set(self._alignment_sequence_rows or ["reference", "moving"])
        normalized = []
        for item in ranges or []:
            if isinstance(item, dict):
                row_name = str(item.get("row") or "")
                start = item.get("start")
                end = item.get("end")
            else:
                try:
                    row_name, start, end = item
                except Exception:
                    continue
            if row_name not in row_names:
                continue
            column_range = self._normalize_column_ranges([(start, end)])
            if not column_range:
                continue
            normalized.append((row_name, column_range[0][0], column_range[0][1]))
        self._search_ranges = normalized
        if 0 <= int(active) < len(self._search_ranges):
            self._search_active = int(active)
        else:
            self._search_active = -1
        self._apply_alignment_styles()

    def scroll_to_column(self, column):
        if self._alignment_length <= 0:
            return
        target = max(0, min(int(column), self._alignment_length - 1))
        primary_row = self._primary_alignment_row()
        line_index = self._alignment_row_lines.get(primary_row, 1)
        if line_index is None or line_index >= len(self._line_starts):
            return
        cursor = QTextCursor(self.document())
        cursor.setPosition(self._line_starts[line_index] + self._alignment_offset + target)
        rect = self.cursorRect(cursor)
        bar = self.horizontalScrollBar()
        viewport_width = max(1, self.viewport().width())
        absolute_x = rect.x() + bar.value()
        target_x = int(absolute_x - viewport_width // 2)
        bar.setValue(max(bar.minimum(), min(bar.maximum(), target_x)))

    def mousePressEvent(self, event):
        metal = self._metal_at(event.pos())
        context_click = (
            event.button() == Qt.MouseButton.RightButton
            or (
                event.button() == Qt.MouseButton.LeftButton
                and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            )
        )
        if metal is not None and context_click and self._metal_context_callback is not None:
            self._drag_anchor = None
            self._drag_current = None
            self._metal_context_callback(metal, self._event_global_pos(event))
            event.accept()
            return
        if metal is not None and event.button() == Qt.MouseButton.LeftButton and self._metal_click_callback is not None:
            self._drag_anchor = None
            self._drag_current = None
            self._metal_click_callback(metal)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            column, row = self._alignment_column_and_row_at(event.pos())
            if column is not None:
                shift_extend = (
                    bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                    and row in self._range_anchor_by_row
                )
                anchor = self._range_anchor_by_row.get(row, column) if shift_extend else column
                self._drag_anchor = anchor
                self._drag_current = column
                self._drag_row = row
                self._drag_shift_extend = shift_extend
                self._preview_range = self._normalized_preview_range(anchor, column)
                self._preview_row = row
                self._apply_alignment_styles()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        column, _row = self._alignment_column_and_row_at(event.pos())
        if self._drag_anchor is not None and bool(event.buttons() & Qt.MouseButton.LeftButton):
            if column is not None:
                if column == self._drag_current:
                    event.accept()
                    return
                self._drag_current = column
                preview = self._normalized_preview_range(self._drag_anchor, self._drag_current)
                if preview == self._preview_range:
                    event.accept()
                    return
                self._preview_range = preview
                self._queue_alignment_styles_apply()
                if self._hover_callback is not None and column != self._hover_column:
                    self._hover_column = column
                    self._hover_callback(column)
            event.accept()
            return
        metal = self._metal_at(event.pos())
        if metal is not None:
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            tooltip = str(metal.get("tooltip") or metal.get("label") or metal.get("spec") or "")
            self.setToolTip(tooltip)
            if self._metal_hover_callback is not None:
                self._metal_hover_callback(metal)
            super().mouseMoveEvent(event)
            return
        self.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor if column is not None else Qt.CursorShape.IBeamCursor
        )
        if column is not None and 0 <= column < len(self._alignment_tooltips):
            self.setToolTip(self._alignment_tooltips[column])
        else:
            self.setToolTip("")
        if column != self._hover_column:
            self._hover_column = column
            if self._hover_callback is not None:
                self._hover_callback(column)
            if column is None and self._metal_hover_callback is not None:
                self._metal_hover_callback(None)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_anchor is not None:
            column, _row = self._alignment_column_and_row_at(event.pos())
            if column is not None:
                self._drag_current = column
            start = self._drag_anchor
            end = self._drag_current if self._drag_current is not None else start
            row = getattr(self, "_drag_row", "reference")
            shift_extend = self._drag_shift_extend
            self._drag_anchor = None
            self._drag_current = None
            self._drag_row = None
            self._drag_shift_extend = False
            self._preview_range = None
            self._preview_row = None
            self._apply_alignment_styles()
            if not shift_extend:
                self._range_anchor_by_row[row] = start
            self._click_callback(start, end, row)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if self._drag_anchor is None:
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        self._hover_column = None
        self.setToolTip("")
        if self._hover_callback is not None:
            self._hover_callback(None)
        if self._metal_hover_callback is not None:
            self._metal_hover_callback(None)
        super().leaveEvent(event)

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

    def _normalized_preview_range(self, start, end):
        if start is None or end is None:
            return None
        start = max(0, min(int(start), self._alignment_length - 1))
        end = max(0, min(int(end), self._alignment_length - 1))
        if end < start:
            start, end = end, start
        return start, end

    def _normalize_column_ranges(self, ranges):
        if self._alignment_length <= 0:
            return []
        normalized = []
        for start, end in ranges or []:
            start = max(0, min(int(start), self._alignment_length - 1))
            end = max(0, min(int(end), self._alignment_length - 1))
            if end < start:
                start, end = end, start
            normalized.append((start, end))
        return _merge_index_ranges(normalized)

    def _alignment_column_and_row_at(self, pos):
        """Return (column, row) for sequence rows or (None, None)."""
        if self._alignment_length <= 0:
            return None, None
        cursor = self.cursorForPosition(pos)
        position = cursor.position()
        primary_row = self._primary_alignment_row()
        # Treat clicks on ruler/match lines as reference-row selections, but
        # keep the column math tied to the sequence start after the left labels.
        row_checks = []
        for row_name in self._alignment_sequence_rows or ["reference", "moving"]:
            row_checks.append((self._alignment_row_lines.get(row_name), row_name))
        for helper_name in ("match", "consensus", "ruler"):
            row_checks.append((self._alignment_row_lines.get(helper_name), primary_row))
        for helper_name, line_index in (self._alignment_row_lines or {}).items():
            if not str(helper_name).endswith("_metal"):
                continue
            parent_row = str(helper_name)[:-6]
            if parent_row in (self._alignment_sequence_rows or []):
                row_checks.append((line_index, parent_row))
        for line_index, row in row_checks:
            if line_index is None or line_index >= len(self._line_starts):
                continue
            col = position - self._line_starts[line_index] - self._alignment_offset
            if 0 <= col < self._alignment_length:
                return col, row
        return None, None

    def _metal_at(self, pos):
        if not self._alignment_metal_ranges:
            return None
        cursor = self.cursorForPosition(pos)
        position = cursor.position()
        for row_name, start, end, metal in self._alignment_metal_ranges:
            line_index = self._alignment_row_lines.get(row_name)
            if line_index is None or line_index >= len(self._line_starts):
                continue
            col = position - self._line_starts[line_index] - self._alignment_offset
            if int(start) <= col < int(end):
                return metal
        return None

    def _apply_alignment_styles(self):
        selections = list(self._base_alignment_style_selections())
        selections.extend(self._selected_column_selections())
        selections.extend(self._search_column_selections())
        if self._preview_range is not None:
            selections.extend(self._preview_selections())
        self.setExtraSelections(selections)

    def _queue_alignment_styles_apply(self):
        if getattr(self, "_alignment_apply_pending", False):
            return
        self._alignment_apply_pending = True

        def apply_later():
            self._alignment_apply_pending = False
            self._apply_alignment_styles()

        QTimer.singleShot(16, apply_later)

    def _base_alignment_style_selections(self):
        key = (
            int(self._alignment_length),
            int(self._alignment_offset),
            tuple(self._alignment_sequence_rows or []),
            tuple(sorted((str(k), int(v)) for k, v in (self._alignment_row_lines or {}).items() if v is not None)),
            tuple((str(row), int(start), int(end), str((metal or {}).get("spec") or "")) for row, start, end, metal in self._alignment_metal_ranges),
            id(self._alignment_styles),
        )
        if key == self._style_selection_cache_key:
            return self._style_selection_cache
        selections = []
        for row_name in self._alignment_sequence_rows or ["reference", "moving"]:
            line_index = self._alignment_row_lines.get(row_name)
            if line_index is None:
                continue
            if line_index >= len(self._line_starts):
                continue
            styles = list(self._alignment_styles.get(row_name) or [])
            if not styles:
                continue
            selections.extend(self._style_selections_for_line(self._line_starts[line_index] + self._alignment_offset, styles))
        selections.extend(self._alignment_metal_token_selections())
        self._style_selection_cache_key = key
        self._style_selection_cache = selections
        return selections

    def _alignment_metal_token_selections(self):
        selections = []
        if not self._alignment_metal_ranges:
            return selections
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#a8e6ff"))
        fmt.setBackground(QColor("#162631"))
        fmt.setFontWeight(900)
        fmt.setFontUnderline(True)
        for row_name, start, end, _metal in self._alignment_metal_ranges:
            line_index = self._alignment_row_lines.get(row_name)
            if line_index is None or line_index >= len(self._line_starts):
                continue
            if end <= start:
                continue
            cursor = QTextCursor(self.document())
            cursor.setPosition(self._line_starts[line_index] + self._alignment_offset + int(start))
            cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, int(end) - int(start))
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = fmt
            selections.append(selection)
        return selections

    def _selected_column_selections(self):
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#005ce6"))
        fmt.setForeground(QColor("#ffffff"))
        fmt.setFontWeight(900)
        fmt.setFontUnderline(True)
        selections = []
        for row_name, ranges in (self._selected_ranges or {}).items():
            line_index = self._alignment_row_lines.get(row_name)
            if line_index is None or line_index >= len(self._line_starts):
                continue
            line_start = self._line_starts[line_index] + self._alignment_offset
            for start, end in ranges or []:
                cursor = QTextCursor(self.document())
                cursor.setPosition(line_start + start)
                cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, end - start + 1)
                selection = QTextEdit.ExtraSelection()
                selection.cursor = cursor
                selection.format = fmt
                selections.append(selection)
        return selections

    def _search_column_selections(self):
        if not self._search_ranges:
            return []
        format_search = QTextCharFormat()
        format_search.setBackground(QColor("#3d3417"))
        format_search.setForeground(QColor("#fce58e"))
        format_search_active = QTextCharFormat()
        format_search_active.setBackground(QColor("#f5c042"))
        format_search_active.setForeground(QColor("#000000"))
        format_search_active.setFontWeight(900)
        selections = []
        for index, (row_name, start, end) in enumerate(self._search_ranges):
            line_index = self._alignment_row_lines.get(row_name)
            if line_index is None or line_index >= len(self._line_starts):
                continue
            line_start = self._line_starts[line_index] + self._alignment_offset
            cursor = QTextCursor(self.document())
            cursor.setPosition(line_start + start)
            cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, end - start + 1)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = format_search_active if index == self._search_active else format_search
            selections.append(selection)
        return selections

    def _preview_selections(self):
        if self._preview_range is None:
            return []
        start, end = self._preview_range
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#ffd23f"))
        fmt.setForeground(QColor("#000000"))
        fmt.setFontWeight(900)
        fmt.setFontUnderline(True)
        selections = []
        sequence_rows = self._alignment_sequence_rows or ["reference", "moving"]
        preview_row = self._preview_row if self._preview_row in sequence_rows else self._primary_alignment_row()
        line_index = self._alignment_row_lines.get(preview_row)
        for line_index in (line_index,):
            if line_index is None or line_index >= len(self._line_starts):
                continue
            cursor = QTextCursor(self.document())
            cursor.setPosition(self._line_starts[line_index] + self._alignment_offset + start)
            cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, end - start + 1)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = fmt
            selections.append(selection)
        return selections

    def _primary_alignment_row(self):
        rows = self._alignment_sequence_rows or []
        if "reference" in rows:
            return "reference"
        return rows[0] if rows else "reference"

    def _style_selections_for_line(self, line_start, styles):
        selections = []
        run_start = None
        run_style = None
        for index in range(self._alignment_length):
            style = _sequence_text_style(styles[index] if index < len(styles) else None)
            if style is None:
                if run_start is not None:
                    selections.append(self._style_selection(line_start, run_start, index - 1, run_style))
                    run_start = None
                    run_style = None
                continue
            if run_style == style:
                continue
            if run_start is not None:
                selections.append(self._style_selection(line_start, run_start, index - 1, run_style))
            run_start = index
            run_style = style
        if run_start is not None:
            selections.append(self._style_selection(line_start, run_start, self._alignment_length - 1, run_style))
        return selections

    def _style_selection(self, line_start, start, end, style):
        cursor = QTextCursor(self.document())
        cursor.setPosition(line_start + start)
        cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, end - start + 1)
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        fmt = QTextCharFormat()
        fg = style.get("foreground")
        bg = style.get("background")
        weight = style.get("font_weight")
        underline = style.get("font_underline")
        if fg is not None:
            fmt.setForeground(fg)
        if bg is not None:
            fmt.setBackground(bg)
        if weight is not None:
            fmt.setFontWeight(int(weight))
        if underline is not None:
            fmt.setFontUnderline(bool(underline))
        selection.format = fmt
        return selection


class CodexSequenceBar(ToolInstance):

    SESSION_ENDURING = False
    SESSION_SAVE = False
    help = "help:user/tools/codex_assistant.html"
    UI_LAYOUT_VERSION = 40

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
        self._selection_refresh_pending = False
        self._suppress_selection_refresh = False
        self._alignment_payload = None
        self._alignment_base_status = ""
        self._sequence_base_status = ""
        self._alignment_selected_columns = {}
        self._wrapper = None
        self._previous_main_view = None
        self.bar_widget = None
        self.handlers = []
        self._search_matches = []
        self._search_active_index = -1
        self._build_ui()

    def _build_ui(self):
        parent = QWidget(self.session.ui.main_window)
        parent.setObjectName("codex_sequence_bar")
        self.bar_widget = parent
        layout = QVBoxLayout()
        layout.setContentsMargins(6, 3, 6, 4)
        layout.setSpacing(3)
        parent.setLayout(layout)
        parent.setMinimumHeight(134)
        parent.setMaximumHeight(220)
        parent.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        parent.setStyleSheet(
            "QWidget#codex_sequence_bar { background: #15181b; }"
            "QLabel { color: #d9dde2; background: transparent; }"
            "QComboBox {"
            " background: #0e1114;"
            " color: #edf0f3;"
            " border: 1px solid #2a2f35;"
            " border-radius: 9px;"
            " padding: 4px 10px;"
            " font-weight: 600;"
            "}"
            "QComboBox:hover { background: #14181c; border-color: #44494f; }"
            "QComboBox:focus { border-color: #6e757d; }"
            "QComboBox::drop-down { border: none; width: 18px; }"
            "QComboBox QAbstractItemView {"
            " background: #14181c;"
            " color: #eef1f4;"
            " border: 1px solid #2a2f35;"
            " border-radius: 8px;"
            " selection-background-color: #2c333a;"
            " padding: 4px;"
            "}"
            "QPushButton {"
            " background: #1d2126;"
            " color: #eef1f4;"
            " border: 1px solid #2a2f35;"
            " border-radius: 9px;"
            " padding: 5px 12px;"
            " font-weight: 600;"
            "}"
            "QToolButton {"
            " background: #1d2126;"
            " color: #eef1f4;"
            " border: 1px solid #2a2f35;"
            " border-radius: 9px;"
            " padding: 5px 12px;"
            " font-weight: 600;"
            "}"
            "QPushButton:hover, QToolButton:hover {"
            " background: #262a30; border-color: #44494f;"
            "}"
            "QPushButton:pressed, QToolButton:pressed {"
            " background: #11141a; border-color: #2a2f35;"
            "}"
            "QToolButton::menu-button { border: none; width: 16px; }"
            "QToolButton::menu-arrow { image: none; }"
            "QPlainTextEdit {"
            " background: #0a0d10;"
            " color: #eef1f4;"
            " border: 1px solid #2a2f35;"
            " border-radius: 10px;"
            " padding: 6px 10px 14px 10px;"
            " selection-background-color: #c7ccd2;"
            " selection-color: #101214;"
            "}"
            "QPlainTextEdit#codex_sequence_alignment {"
            " background: #0e1115;"
            " color: #eef1f4;"
            " border: 1px solid #2a2f35;"
            " border-radius: 10px;"
            " padding: 6px 10px;"
            "}"
            "QPlainTextEdit#codex_sequence_metals {"
            " background: #0a0d10;"
            " color: #f2f5f8;"
            " border: 1px solid #2a2f35;"
            " border-radius: 8px;"
            " padding: 4px 10px;"
            " font-weight: 800;"
            " selection-background-color: #c7ccd2;"
            " selection-color: #101214;"
            "}"
            "QLineEdit#codex_sequence_search {"
            " background: #0e1114;"
            " color: #edf0f3;"
            " border: 1px solid #2a2f35;"
            " border-radius: 9px;"
            " padding: 4px 9px;"
            " selection-background-color: #2c333a;"
            " selection-color: #eef1f4;"
            "}"
            "QLineEdit#codex_sequence_search:focus { border-color: #6e757d; }"
            "QLineEdit#codex_sequence_search:disabled {"
            " background: #0a0c0f; color: #5b6168; border-color: #1f2328;"
            "}"
            "QScrollBar#codex_sequence_scroll:horizontal {"
            " height: 12px; background: transparent; margin: 0 4px; border: none;"
            "}"
            "QScrollBar#codex_sequence_scroll::handle:horizontal {"
            " background: #3a3f46; border-radius: 5px; min-width: 32px;"
            "}"
            "QScrollBar#codex_sequence_scroll::handle:horizontal:hover {"
            " background: #54595f;"
            "}"
            "QScrollBar#codex_sequence_scroll::add-line:horizontal,"
            "QScrollBar#codex_sequence_scroll::sub-line:horizontal { width: 0; height: 0; }"
            "QScrollBar#codex_sequence_scroll::add-page:horizontal,"
            "QScrollBar#codex_sequence_scroll::sub-page:horizontal { background: transparent; }"
            "QMenu {"
            " background: #14181c;"
            " color: #eef1f4;"
            " border: 1px solid #2a2f35;"
            " border-radius: 8px;"
            " padding: 4px;"
            "}"
            "QMenu::item {"
            " background: transparent;"
            " padding: 6px 14px;"
            " border-radius: 6px;"
            "}"
            "QMenu::item:selected { background: #2c333a; }"
            "QMenu::separator {"
            " height: 1px;"
            " background: #2a2f35;"
            " margin: 4px 8px;"
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
        self.selection_button.setToolTip(
            "Click to toggle Residue ↔ Chain selection mode. Use the dropdown arrow for helix/sheet actions."
        )
        self.selection_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.selection_button.setMenu(self._build_selection_menu(parent))
        self.selection_button.clicked.connect(self._cycle_click_mode)
        top_row.addWidget(self.selection_button, 0)

        self.status_label = QLabel("No protein sequence loaded.", parent)
        self.status_label.setMinimumWidth(0)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        top_row.addWidget(self.status_label, 1)

        self.search_edit = QLineEdit(parent)
        self.search_edit.setObjectName("codex_sequence_search")
        self.search_edit.setPlaceholderText("Find seq (X = any)…")
        self.search_edit.setToolTip(
            "Find a subsequence in the current chain. X matches any residue. "
            "Press Enter to jump to the next match."
        )
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedWidth(170)
        self.search_edit.textChanged.connect(self._on_search_text_changed)
        self.search_edit.returnPressed.connect(self._on_search_next)
        for key in ("Shift+Return", "Shift+Enter"):
            shortcut = QShortcut(QKeySequence(key), self.search_edit)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self._on_search_prev)
        top_row.addWidget(self.search_edit, 0)

        self.search_prev_button = QToolButton(parent)
        self.search_prev_button.setText("◀")
        self.search_prev_button.setToolTip("Previous match (Shift+Enter)")
        self.search_prev_button.setAutoRaise(False)
        self.search_prev_button.setFixedWidth(28)
        self.search_prev_button.clicked.connect(self._on_search_prev)
        top_row.addWidget(self.search_prev_button, 0)

        self.search_next_button = QToolButton(parent)
        self.search_next_button.setText("▶")
        self.search_next_button.setToolTip("Next match (Enter)")
        self.search_next_button.setAutoRaise(False)
        self.search_next_button.setFixedWidth(28)
        self.search_next_button.clicked.connect(self._on_search_next)
        top_row.addWidget(self.search_next_button, 0)

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
            clear_callback=self._clear_selection_from_panel,
            metal_click_callback=self._select_metal_token,
            metal_context_callback=self._show_metal_context_menu,
            metal_hover_callback=self._show_metal_hover,
        )
        self.sequence_text.setFixedHeight(78)
        layout.addWidget(self.sequence_text)

        self.sequence_scrollbar = QScrollBar(Qt.Orientation.Horizontal, parent)
        self.sequence_scrollbar.setObjectName("codex_sequence_scroll")
        self.sequence_scrollbar.setFixedHeight(12)
        layout.addWidget(self.sequence_scrollbar)
        internal_bar = self.sequence_text.horizontalScrollBar()
        internal_bar.rangeChanged.connect(self._sync_sequence_scrollbar)
        internal_bar.valueChanged.connect(self.sequence_scrollbar.setValue)
        self.sequence_scrollbar.valueChanged.connect(internal_bar.setValue)
        self._sync_sequence_scrollbar(internal_bar.minimum(), internal_bar.maximum())

        self.alignment_text = ClickableAlignmentText(
            self._select_alignment_range_by_column,
            self._show_alignment_hover,
            parent,
            metal_click_callback=self._select_metal_token,
            metal_context_callback=self._show_metal_context_menu,
            metal_hover_callback=self._show_metal_hover,
        )
        self.alignment_text.setObjectName("codex_sequence_alignment")
        self.alignment_text.setReadOnly(True)
        self.alignment_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.alignment_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.alignment_text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.alignment_text.setFont(_sequence_panel_font())
        self.alignment_text.setFixedHeight(0)
        self.alignment_text.setVisible(False)
        layout.addWidget(self.alignment_text)

        self._relax_min_size()
        self._attach_to_graphics_view()
        self.install_handlers()
        self.refresh()

    def _relax_min_size(self):
        if self.bar_widget is None:
            return
        self.bar_widget.setMinimumWidth(0)
        for child in self.bar_widget.findChildren(QWidget):
            try:
                child.setMinimumWidth(0)
            except Exception:
                pass
        for child_layout in self.bar_widget.findChildren(QLayout):
            try:
                child_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
            except Exception:
                pass

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
            self._relax_wrapper(wrapper)
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
        self._relax_wrapper(wrapper)

    def _relax_wrapper(self, wrapper):
        try:
            wrapper.setMinimumWidth(0)
            wrapper.setMinimumSize(0, 0)
            wrapper.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
            )
            layout = wrapper.layout()
            if layout is not None:
                layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        except Exception:
            pass
        if self.bar_widget is not None:
            try:
                self.bar_widget.setSizePolicy(
                    QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
                )
            except Exception:
                pass

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
            self.session.triggers.add_handler("command finished", self._queue_command_refresh),
            self.session.triggers.add_handler("selection changed", self._queue_selection_refresh),
        ]
        for trigger_name in ("model display changed", "add models", "remove models"):
            try:
                self.handlers.append(
                    self.session.triggers.add_handler(trigger_name, self._queue_refresh)
                )
            except Exception:
                pass

    def delete(self):
        for handler in self.handlers:
            try:
                self.session.triggers.remove_handler(handler)
            except Exception:
                pass
        self.handlers = []
        self._detach_from_graphics_view()
        super().delete()

    def _selected_chain_specs_from_session(self):
        specs = set()
        try:
            from chimerax.atomic import selected_residues

            for residue in selected_residues(self.session):
                structure = getattr(residue, "structure", None)
                model_id = getattr(structure, "id_string", None)
                chain_id = str(getattr(residue, "chain_id", "") or "").strip()
                if model_id and chain_id:
                    specs.add(f"#{model_id}/{chain_id}")
        except Exception:
            pass
        if specs:
            return specs
        try:
            from chimerax.atomic import selected_atoms

            for atom in selected_atoms(self.session):
                residue = getattr(atom, "residue", None)
                structure = getattr(residue, "structure", None)
                model_id = getattr(structure, "id_string", None)
                chain_id = str(getattr(residue, "chain_id", "") or "").strip()
                if model_id and chain_id:
                    specs.add(f"#{model_id}/{chain_id}")
        except Exception:
            pass
        return specs

    def _selected_residue_keys_from_session(self):
        keys = set()
        try:
            from chimerax.atomic import selected_residues

            for residue in selected_residues(self.session):
                keys.update(self._residue_object_keys(residue))
        except Exception:
            pass
        if keys:
            return keys
        try:
            from chimerax.atomic import selected_atoms

            for atom in selected_atoms(self.session):
                keys.update(self._residue_object_keys(getattr(atom, "residue", None)))
        except Exception:
            pass
        return keys

    def _residue_object_keys(self, residue):
        keys = set()
        if residue is None:
            return keys
        spec = str(getattr(residue, "atomspec", "") or "").strip()
        if spec:
            keys.add(spec)
            keys.add(self._canonical_residue_key_from_spec(spec))
        structure = getattr(residue, "structure", None)
        model_id = getattr(structure, "id_string", None)
        chain_id = str(getattr(residue, "chain_id", "") or "").strip()
        number = str(getattr(residue, "number", "") or "").strip()
        if model_id and chain_id and number:
            keys.add(f"#{model_id}/{chain_id}:{number}")
            keys.add(f"#{model_id}|{chain_id}|{number}")
        return {key for key in keys if key}

    def _canonical_residue_key_from_spec(self, spec):
        spec = str(spec or "").strip()
        if not spec or ":" not in spec:
            return ""
        model_chain, number = spec.rsplit(":", 1)
        if "/" not in model_chain:
            return ""
        model_spec, chain_id = model_chain.rsplit("/", 1)
        return f"{model_spec}|{chain_id}|{number}"

    def _residue_spec_matches_keys(self, spec, selected_keys):
        spec = str(spec or "").strip()
        if not spec:
            return False
        return spec in selected_keys or self._canonical_residue_key_from_spec(spec) in selected_keys

    def _indices_to_ranges(self, indices):
        values = sorted(set(int(index) for index in indices or []))
        if not values:
            return []
        ranges = []
        start = previous = values[0]
        for index in values[1:]:
            if index == previous + 1:
                previous = index
                continue
            ranges.append((start, previous))
            start = previous = index
        ranges.append((start, previous))
        return ranges

    def _alignment_selected_columns_from_session(self, payload):
        selected_keys = self._selected_residue_keys_from_session()
        row_items = _alignment_payload_row_items(payload)
        if not selected_keys:
            return {row_name: [] for row_name, _side in row_items}
        result = {}
        for row_name, side in row_items:
            columns = []
            for column, spec in enumerate(side.get("column_map") or []):
                if self._residue_spec_matches_keys(spec, selected_keys):
                    columns.append(column)
            result[row_name] = self._indices_to_ranges(columns)
        return result

    def _first_alignment_selected_column(self, selected_columns):
        starts = []
        for ranges in (selected_columns or {}).values():
            starts.extend(start for start, _end in ranges or [])
        return min(starts) if starts else None

    def _alignment_selected_status(self, payload, selected_columns):
        first_column = self._first_alignment_selected_column(selected_columns)
        if first_column is None:
            return ""
        statuses = list(payload.get("display_statuses") or [])
        if not (0 <= first_column < len(statuses)):
            return ""
        total = sum((end - start + 1) for ranges in (selected_columns or {}).values() for start, end in (ranges or []))
        prefix = "Selected"
        if total > 1:
            prefix = f"Selected {total} alignment residue(s)"
        return f"{prefix} · {statuses[first_column]}"

    def refresh(self):
        previous_spec = self._current_entry.get("spec") if self._current_entry else None
        self._entries = _protein_sequence_entries(self.session)
        self.chain_combo.blockSignals(True)
        self.chain_combo.clear()
        for entry in self._entries:
            self.chain_combo.addItem(entry["display"], entry["spec"])

        selected_models = {
            entry["model_spec"]
            for entry in self._entries
            if entry.get("selected")
        }
        selected_chain_specs = self._selected_chain_specs_from_session()
        displayed_models = {
            entry["model_spec"]
            for entry in self._entries
            if entry.get("displayed")
        }

        if self._entries:
            index = 0
            if previous_spec:
                found = self.chain_combo.findData(previous_spec)
                if found >= 0:
                    index = found

            if len(selected_chain_specs) == 1:
                found = self.chain_combo.findData(next(iter(selected_chain_specs)))
                if found >= 0:
                    index = found

            previous_entry = next(
                (entry for entry in self._entries if entry.get("spec") == previous_spec),
                None,
            )
            current_model = previous_entry["model_spec"] if previous_entry else None

            auto_target = None
            if len(selected_models) == 1:
                auto_target = next(iter(selected_models))
            elif len(displayed_models) == 1:
                auto_target = next(iter(displayed_models))

            if len(selected_chain_specs) != 1 and auto_target is not None and current_model != auto_target:
                target_entry = next(
                    (
                        entry
                        for entry in self._entries
                        if entry["model_spec"] == auto_target
                    ),
                    None,
                )
                if target_entry is not None:
                    found = self.chain_combo.findData(target_entry["spec"])
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

    def _queue_command_refresh(self, _trigger_name=None, command=None, *_args):
        if self._is_selection_only_command(command):
            self._queue_selection_refresh()
            return
        self._queue_refresh()

    def _queue_selection_refresh(self, *_args):
        if getattr(self, "_suppress_selection_refresh", False):
            return
        if self._selection_refresh_pending:
            return
        self._selection_refresh_pending = True

        def run_refresh():
            self._selection_refresh_pending = False
            self._refresh_selection_state()

        QTimer.singleShot(90, run_refresh)

    def _mark_internal_selection_update(self, delay_ms=220):
        self._suppress_selection_refresh = True

        def clear_suppression():
            self._suppress_selection_refresh = False

        QTimer.singleShot(delay_ms, clear_suppression)

    def _is_selection_only_command(self, command):
        text = str(command or "").strip().lower()
        if not text:
            return False
        return text.startswith("select ") or text == "select" or text.startswith("~select")

    def _refresh_selection_state(self):
        if not self._entries:
            return
        selected_chain_specs = self._selected_chain_specs_from_session()
        if len(selected_chain_specs) == 1:
            selected_spec = next(iter(selected_chain_specs))
            current_spec = self._current_entry.get("spec") if self._current_entry else None
            if selected_spec != current_spec:
                found = self.chain_combo.findData(selected_spec)
                if found >= 0:
                    self.chain_combo.blockSignals(True)
                    self.chain_combo.setCurrentIndex(found)
                    self.chain_combo.blockSignals(False)
                    self._set_current_entry_from_combo()
                    return

        if self._alignment_payload and self.alignment_text.isVisible():
            self._refresh_alignment_selection_highlights(scroll=True, update_status=True)
            return
        self._refresh_sequence_selection_highlights(self._current_entry, scroll=True, update_status=True)

    def _refresh_sequence_selection_highlights(self, entry=None, *, scroll=False, update_status=True):
        entry = entry or self._current_entry
        if not entry:
            return []
        selected_ranges = self._selected_ranges_from_session(entry)
        if selected_ranges != self._selected_ranges:
            self._selected_ranges = selected_ranges
            self.sequence_text.highlight_ranges(selected_ranges)
        if scroll and selected_ranges:
            self.sequence_text.scroll_to_index(selected_ranges[0][0])
        if update_status and selected_ranges:
            self.status_label.setText(self._status_for_ranges(entry, selected_ranges))
        elif update_status and not selected_ranges and not (self._alignment_payload and self.alignment_text.isVisible()):
            self.status_label.setText(self._sequence_base_status or f"{entry['spec']} · {entry['length']} residues")
        return selected_ranges

    def _refresh_alignment_selection_highlights(self, payload=None, *, scroll=False, update_status=True):
        payload = payload or self._alignment_payload
        if not payload:
            return {}
        selected_columns = self._alignment_selected_columns_from_session(payload)
        if selected_columns != self._alignment_selected_columns:
            self._alignment_selected_columns = selected_columns
            self.alignment_text.highlight_columns(selected_columns)
        first_selected_column = self._first_alignment_selected_column(selected_columns)
        if scroll and first_selected_column is not None:
            self.alignment_text.scroll_to_column(first_selected_column)
        if update_status:
            selected_status = self._alignment_selected_status(payload, selected_columns)
            self.status_label.setText(selected_status or getattr(self, "_alignment_base_status", ""))
        return selected_columns

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
            ("chain", "Whole chain"),
            ("atom", "Cα atoms only"),
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
        chain_type_menu = menu.addMenu("By residue type (chain)")
        self._populate_residue_type_menu(chain_type_menu, scope="chain")
        model_type_menu = menu.addMenu("By residue type (whole model)")
        self._populate_residue_type_menu(model_type_menu, scope="model")
        menu.addSeparator()
        menu.addAction(
            "Find Cys disulfide candidates (≤ 6 Å, chain)",
            lambda: self._find_disulfide_candidates(scope="chain"),
        )
        menu.addAction(
            "Find Cys disulfide candidates (≤ 6 Å, model)",
            lambda: self._find_disulfide_candidates(scope="model"),
        )
        menu.addSeparator()
        named_groups = list(getattr(self.session, "_codex_named_selections", []) or [])
        menu.addAction(
            "Save selection as named group…",
            self._save_selection_as_named_group,
        )
        if named_groups:
            apply_menu = menu.addMenu(f"Apply named group ({len(named_groups)})")
            for name in named_groups:
                apply_menu.addAction(
                    name,
                    lambda checked=False, n=name: self._apply_named_group(n),
                )
            rename_menu = menu.addMenu("Rename group")
            for name in named_groups:
                rename_menu.addAction(
                    name,
                    lambda checked=False, n=name: self._rename_named_group(n),
                )
            delete_menu = menu.addMenu("Delete group")
            for name in named_groups:
                delete_menu.addAction(
                    name,
                    lambda checked=False, n=name: self._delete_named_group(n),
                )
        menu.addSeparator()
        menu.addAction("FoldDisco from selected residues", self._open_folddisco)

    def _named_groups(self):
        from .named_selection import list_groups

        return list_groups(self.session)

    def _build_concrete_selection_spec(self):
        try:
            from chimerax.atomic import selected_residues
        except Exception:
            return None
        try:
            selected = selected_residues(self.session)
        except Exception:
            return None
        if selected is None or len(selected) == 0:
            return None
        try:
            chain_groups = list(selected.by_chain)
        except Exception:
            return None
        parts = []
        for structure, chain_id, residues in chain_groups:
            model_spec = f"#{getattr(structure, 'id_string', '?')}"
            chain_label = str(chain_id or "?").strip() or "?"
            try:
                numbers = sorted({int(n) for n in residues.numbers})
            except Exception:
                continue
            if not numbers:
                continue
            ranges = []
            start = prev = numbers[0]
            for n in numbers[1:]:
                if n == prev + 1:
                    prev = n
                    continue
                ranges.append((start, prev))
                start = prev = n
            ranges.append((start, prev))
            range_str = ",".join(
                f"{s}-{e}" if s != e else f"{s}" for s, e in ranges
            )
            parts.append(f"{model_spec}/{chain_label}:{range_str}")
        return " ".join(parts) if parts else None

    def _save_selection_as_named_group(self):
        from Qt.QtWidgets import QInputDialog

        spec = self._build_concrete_selection_spec()
        if not spec:
            self.status_label.setText(
                "No selection to save. Select residues first, then try again."
            )
            return

        name, ok = QInputDialog.getText(
            self.session.ui.main_window,
            "Save selection",
            "Group name (letters, digits, underscore):",
        )
        if not ok:
            return
        name = (name or "").strip().replace(" ", "_")
        if not name or not all(ch.isalnum() or ch == "_" for ch in name):
            self.status_label.setText("Invalid group name. Use letters, digits, or underscore.")
            return

        try:
            from chimerax.core.commands import run

            run(self.session, f"name {name} {spec}")
        except Exception as err:
            message = str(err) if str(err) else err.__class__.__name__
            self.session.logger.error(f"Save named group failed: {message}")
            self.status_label.setText(f"Save failed: {message}")
            return

        from .named_selection import add_group

        add_group(self.session, name, spec)
        self.status_label.setText(
            f"Saved ‘{name}’ ({spec}). Visible in Models panel + Selection menu."
        )

    def _apply_named_group(self, name):
        try:
            from chimerax.core.commands import run

            run(self.session, f"select {name}")
        except Exception as err:
            message = str(err) if str(err) else err.__class__.__name__
            self.session.logger.error(f"Apply named group failed: {message}")
            self.status_label.setText(f"Apply failed: {message}")
            return
        self.status_label.setText(f"Selected named group ‘{name}’.")

    def _rename_named_group(self, name):
        from Qt.QtWidgets import QInputDialog

        new_name, ok = QInputDialog.getText(
            self.session.ui.main_window,
            "Rename group",
            f"New name for ‘{name}’:",
            text=name,
        )
        if not ok:
            return
        new_name = (new_name or "").strip().replace(" ", "_")
        if not new_name or not all(ch.isalnum() or ch == "_" for ch in new_name):
            self.status_label.setText("Invalid group name.")
            return
        if new_name == name:
            return

        from .named_selection import get_group_spec, rename_group

        spec = get_group_spec(self.session, name)
        if not spec:
            self.status_label.setText(f"Group ‘{name}’ no longer exists.")
            return
        try:
            from chimerax.core.commands import run

            run(self.session, f"name {new_name} {spec}")
            run(self.session, f"~name {name}")
        except Exception as err:
            message = str(err) if str(err) else err.__class__.__name__
            self.session.logger.error(f"Rename named group failed: {message}")
            self.status_label.setText(f"Rename failed: {message}")
            return

        rename_group(self.session, name, new_name)
        self.status_label.setText(f"Renamed ‘{name}’ → ‘{new_name}’.")

    def _delete_named_group(self, name):
        try:
            from chimerax.core.commands import run

            run(self.session, f"~name {name}")
        except Exception as err:
            self.session.logger.warning(f"~name {name} failed: {err}")
        from .named_selection import remove_group

        remove_group(self.session, name)
        self.status_label.setText(f"Deleted named group ‘{name}’.")

    _RESIDUE_TYPE_GROUPS = (
        ("Acidic (D, E)", "asp,glu"),
        ("Basic (K, R, H)", "lys,arg,his"),
        ("Aromatic (F, W, Y)", "phe,trp,tyr"),
        ("Polar uncharged (S, T, N, Q)", "ser,thr,asn,gln"),
        ("Hydrophobic (A, V, L, I, M)", "ala,val,leu,ile,met"),
        ("Glycine (G)", "gly"),
        ("Proline (P)", "pro"),
        ("Cysteine (C)", "cys"),
        ("Histidine (H)", "his"),
        ("Methionine (M)", "met"),
    )

    _SINGLE_AMINO_ACIDS = (
        ("Ala (A)", "ala"), ("Arg (R)", "arg"), ("Asn (N)", "asn"),
        ("Asp (D)", "asp"), ("Cys (C)", "cys"), ("Gln (Q)", "gln"),
        ("Glu (E)", "glu"), ("Gly (G)", "gly"), ("His (H)", "his"),
        ("Ile (I)", "ile"), ("Leu (L)", "leu"), ("Lys (K)", "lys"),
        ("Met (M)", "met"), ("Phe (F)", "phe"), ("Pro (P)", "pro"),
        ("Ser (S)", "ser"), ("Thr (T)", "thr"), ("Trp (W)", "trp"),
        ("Tyr (Y)", "tyr"), ("Val (V)", "val"),
    )

    def _populate_residue_type_menu(self, menu, scope):
        for label, code in self._RESIDUE_TYPE_GROUPS:
            menu.addAction(
                label,
                lambda checked=False, c=code, l=label, s=scope: self._select_by_residue_type(c, l, s),
            )
        menu.addSeparator()
        single_menu = menu.addMenu("Single amino acid")
        for label, code in self._SINGLE_AMINO_ACIDS:
            single_menu.addAction(
                label,
                lambda checked=False, c=code, l=label, s=scope: self._select_by_residue_type(c, l, s),
            )

    def _select_by_residue_type(self, residue_codes, label, scope="chain"):
        entry = self._current_entry
        if not entry:
            self.status_label.setText("No protein chain resolved.")
            return
        base_spec = entry["spec"] if scope == "chain" else entry["model_spec"]
        target_spec = f"({base_spec}) & :{residue_codes}"
        scope_label = "chain" if scope == "chain" else "model"
        try:
            from chimerax.core.commands import run

            with command_batch(self.session, f"Select {label} ({scope_label})"):
                run(self.session, f"select {target_spec}")
        except Exception as err:
            message = str(err) if str(err) else err.__class__.__name__
            self.session.logger.error(f"Residue-type selection failed: {message}")
            return
        self.status_label.setText(f"Selected {label} in {base_spec}.")

    def _find_disulfide_candidates(self, threshold=6.0, scope="chain"):
        entry = self._current_entry
        if not entry:
            self.status_label.setText("No protein chain resolved.")
            return
        base_spec = entry["spec"] if scope == "chain" else entry["model_spec"]

        try:
            from chimerax.core.commands import ObjectsArg
        except Exception:
            self.status_label.setText("ChimeraX command parser unavailable.")
            return

        try:
            objects, _used, _rest = ObjectsArg.parse(f"({base_spec}) & :cys@SG", self.session)
        except Exception as err:
            self.status_label.setText(f"Cys SG query failed: {err}")
            return

        sg_atoms = getattr(objects, "atoms", None)
        if sg_atoms is None or len(sg_atoms) < 2:
            count = 0 if sg_atoms is None else len(sg_atoms)
            self.status_label.setText(
                f"Only {count} Cys SG atom(s) in {base_spec}; need ≥ 2 for disulfide candidates."
            )
            return

        try:
            import numpy as np
        except ImportError:
            self.status_label.setText("numpy unavailable for distance calculation.")
            return

        try:
            coords = np.asarray(sg_atoms.scene_coords)
        except Exception:
            coords = np.asarray([atom.scene_coord for atom in sg_atoms])
        n = len(coords)
        pairs = []
        paired = set()
        for i in range(n):
            for j in range(i + 1, n):
                d = float(np.linalg.norm(coords[i] - coords[j]))
                if d <= threshold:
                    pairs.append((sg_atoms[i].residue, sg_atoms[j].residue, d))
                    paired.add(i)
                    paired.add(j)

        if not pairs:
            self.status_label.setText(
                f"No Cys pairs within {threshold:.1f} Å in {base_spec}."
            )
            return

        seen = set()
        residue_specs = []
        for index in sorted(paired):
            r = sg_atoms[index].residue
            key = (getattr(r.structure, "id_string", "?"), r.chain_id, r.number)
            if key in seen:
                continue
            seen.add(key)
            residue_specs.append(
                f"#{getattr(r.structure, 'id_string', '?')}/{r.chain_id}:{r.number}"
            )

        spec_text = " ".join(residue_specs)
        pair_summary = ", ".join(
            f"{p[0].chain_id}{p[0].number}-{p[1].chain_id}{p[1].number} ({p[2]:.1f}Å)"
            for p in sorted(pairs, key=lambda item: item[2])[:6]
        )
        if len(pairs) > 6:
            pair_summary += f", +{len(pairs) - 6}"

        self._run_commands(
            f"Disulfide candidates ≤ {threshold:.1f} Å in {base_spec}",
            [
                f"select {spec_text}",
                f"show {spec_text} atoms",
                f"style {spec_text} stick",
            ],
        )
        self.status_label.setText(
            f"{len(pairs)} Cys pair(s) ≤ {threshold:.1f} Å: {pair_summary}"
        )

    def _set_selection_click_mode(self, mode):
        if mode not in ("residue", "chain", "atom"):
            mode = "residue"
        self._selection_click_mode = mode
        self.session._codex_bridge_selection_click_mode = mode
        if mode != "chain":
            self.session._codex_bridge_last_chain_selection_spec = ""
        label = {
            "residue": "Residue",
            "chain": "Chain",
            "atom": "Atom",
        }[mode]
        self.selection_button.setText(f"Select: {label}")
        action_text = {
            "residue": "Sequence + 3D click → residue. Click the button to cycle modes.",
            "chain": "Sequence + 3D click → whole chain. Click the button to cycle modes.",
            "atom": "Sequence click → Cα atom; 3D click → residue. Click the button to cycle modes.",
        }[mode]
        self.status_label.setText(action_text)
        menu = self.selection_button.menu() if hasattr(self, "selection_button") else None
        if menu is not None:
            self._populate_selection_menu(menu)
        try:
            from .pick_mode import bind_pick_mode

            pick_target = "chain" if mode == "chain" else "residue"
            message = bind_pick_mode(self.session, pick_target)
            self.session.logger.info(
                f"[Codex seq-mode] click_mode={mode!r} → 3D pick_mode={pick_target!r} ({message})"
            )
        except Exception as err:
            self.session.logger.warning(
                f"[Codex seq-mode] failed to rebind 3D pick mode: {err}"
            )

    def _cycle_click_mode(self):
        cycle = ("residue", "chain", "atom")
        try:
            index = cycle.index(self._selection_click_mode)
        except ValueError:
            index = -1
        self._set_selection_click_mode(cycle[(index + 1) % len(cycle)])

    def _render_sequence(self):
        entry = self._current_entry
        if not entry:
            self.status_label.setText("No protein chain resolved. Open/select a protein model.")
            self.sequence_text.set_sequence_text([], 0, [])
            self._render_metal_panel(None)
            self._render_alignment_panel(None)
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
        metal_text = _metal_summary_text(entry)
        annotation_text = f"{motif_text} · {metal_text}" if metal_text else motif_text
        self._sequence_base_status = (
            f"{entry['spec']} · {entry['length']} residues · click/drag adds; Shift-click extends; "
            f"click selected residue to remove; click empty area to clear · right-click menu · {annotation_text}"
        )
        self.status_label.setText(self._sequence_base_status)
        metal_entries = _metal_panel_entries_for_entry(entry)
        metal_suffix, metal_ranges = _sequence_metal_suffix(metal_entries, entry["length"])
        lines = [
            _tick_line(entry["length"]),
            entry["sequence"] + metal_suffix,
        ]
        motif_line = _motif_line(entry, motifs)
        if motif_line:
            lines.append(motif_line)
        try:
            self.sequence_text.setFixedHeight(max(78, min(100, 34 + (len(lines) * 18))))
        except Exception:
            pass
        self.sequence_text.set_sequence_text(
            lines,
            entry["length"],
            entry["tooltips"],
            entry.get("residue_styles"),
            metal_ranges,
        )
        selected_ranges = self._selected_ranges_from_session(entry)
        self._selected_ranges = selected_ranges
        self.sequence_text.highlight_ranges(selected_ranges)
        self._refresh_search_highlights()
        if selected_ranges:
            self.status_label.setText(self._status_for_ranges(entry, selected_ranges))
        self._render_alignment_panel(entry)

    def _render_metal_panel(self, entry):
        if not hasattr(self, "metal_text"):
            return
        self.metal_text.set_metals(
            _metal_panel_entries_for_entry(entry),
            sequence_length=int((entry or {}).get("length", 0) or 0),
        )

    def _compute_search_matches(self, query):
        if self._search_targets_alignment():
            return self._compute_alignment_search_matches(query)
        entry = self._current_entry
        if not entry:
            return []
        sequence = str(entry.get("sequence") or "")
        if not sequence:
            return []
        cleaned = "".join(ch for ch in str(query or "").upper() if ch.isalpha())
        if not cleaned:
            return []
        import re

        pattern = "".join("." if ch == "X" else re.escape(ch) for ch in cleaned)
        try:
            rx = re.compile(pattern)
        except re.error:
            return []
        return [(m.start(), m.end() - 1) for m in rx.finditer(sequence) if m.end() > m.start()]

    def _search_targets_alignment(self):
        return bool(
            getattr(self, "_alignment_payload", None)
            and hasattr(self, "alignment_text")
            and self.alignment_text.isVisible()
        )

    def _clean_search_query(self, query):
        return "".join(ch for ch in str(query or "").upper() if ch.isalpha())

    def _search_regex_for_query(self, query):
        cleaned = self._clean_search_query(query)
        if not cleaned:
            return None
        import re

        pattern = "".join("." if ch == "X" else re.escape(ch) for ch in cleaned)
        try:
            return re.compile(pattern)
        except re.error:
            return None

    def _compute_alignment_search_matches(self, query):
        payload = getattr(self, "_alignment_payload", None)
        rx = self._search_regex_for_query(query)
        if not payload or rx is None:
            return []
        matches = []
        for row_name, side in _alignment_payload_row_items(payload):
            aligned = str(side.get("aligned") or "")
            if not aligned:
                continue
            ungapped = []
            columns = []
            for column, char in enumerate(aligned):
                if _is_alignment_gap(char):
                    continue
                ungapped.append(char.upper())
                columns.append(column)
            sequence = "".join(ungapped)
            if not sequence:
                continue
            for match in rx.finditer(sequence):
                if match.end() <= match.start():
                    continue
                start_column = columns[match.start()]
                end_column = columns[match.end() - 1]
                matches.append(
                    {
                        "row": row_name,
                        "start": start_column,
                        "end": end_column,
                        "label": self._alignment_search_match_label(
                            row_name, side, aligned, start_column, end_column
                        ),
                    }
                )
        return matches

    def _alignment_search_match_label(self, row_name, side, aligned, start_column, end_column):
        label = side.get("display") or side.get("spec") or row_name
        start_text = _alignment_side_residue_text(side, aligned, start_column)
        end_text = _alignment_side_residue_text(side, aligned, end_column)
        if start_column == end_column or start_text == end_text:
            return f"{label} · {start_text}"
        return f"{label} · {start_text} to {end_text}"

    def _refresh_search_highlights(self):
        if not hasattr(self, "search_edit"):
            return
        query = self.search_edit.text().strip()
        matches = self._compute_search_matches(query) if query else []
        if (
            matches == self._search_matches
            and 0 <= self._search_active_index < len(matches)
        ):
            active = self._search_active_index
        else:
            active = 0 if matches else -1
        self._search_matches = matches
        self._search_active_index = active
        if self._search_targets_alignment():
            self.sequence_text.set_search_ranges([], -1)
            self.alignment_text.set_search_ranges(matches, active)
        else:
            self.alignment_text.set_search_ranges([], -1)
            self.sequence_text.set_search_ranges(matches, active)

    def _on_search_text_changed(self, text):
        query = (text or "").strip()
        if not query:
            self._search_matches = []
            self._search_active_index = -1
            self.sequence_text.set_search_ranges([], -1)
            self.alignment_text.set_search_ranges([], -1)
            return
        if not self._current_entry:
            self.status_label.setText("Find: no protein chain loaded.")
            return
        matches = self._compute_search_matches(query)
        self._search_matches = matches
        self._search_active_index = 0 if matches else -1
        if self._search_targets_alignment():
            self.sequence_text.set_search_ranges([], -1)
            self.alignment_text.set_search_ranges(matches, self._search_active_index)
        else:
            self.alignment_text.set_search_ranges([], -1)
            self.sequence_text.set_search_ranges(matches, self._search_active_index)
        if not matches:
            scope = "alignment" if self._search_targets_alignment() else self._current_entry["spec"]
            self.status_label.setText(f"Find “{query}”: no matches in {scope}.")
            return
        if self._search_targets_alignment():
            self.alignment_text.scroll_to_column(matches[0]["start"])
        else:
            first_start, _ = matches[0]
            self.sequence_text.scroll_to_index(first_start)
        label = self._search_match_status_label(matches[0])
        self.status_label.setText(
            f"Find “{query}”: {len(matches)} match(es) · 1/{len(matches)} · {label}"
        )

    def _on_search_next(self):
        self._step_search(+1)

    def _on_search_prev(self):
        self._step_search(-1)

    def _step_search(self, delta):
        if not self._search_matches:
            return
        count = len(self._search_matches)
        self._search_active_index = (self._search_active_index + delta) % count
        if self._search_targets_alignment():
            self.alignment_text.set_search_ranges(self._search_matches, self._search_active_index)
            match = self._search_matches[self._search_active_index]
            self.alignment_text.scroll_to_column(match["start"])
        else:
            self.sequence_text.set_search_ranges(self._search_matches, self._search_active_index)
            start, _end = self._search_matches[self._search_active_index]
            self.sequence_text.scroll_to_index(start)
            match = self._search_matches[self._search_active_index]
        label = self._search_match_status_label(match)
        query = self.search_edit.text().strip()
        self.status_label.setText(
            f"Find “{query}”: {self._search_active_index + 1}/{count} · {label}"
        )

    def _search_match_status_label(self, match):
        if isinstance(match, dict):
            return str(match.get("label") or f"column {int(match.get('start', 0)) + 1}")
        entry = self._current_entry
        start, end = match
        return self._short_range_label(entry, start, end) if entry else f"{start + 1}-{end + 1}"

    def _sync_sequence_scrollbar(self, mn, mx):
        bar = getattr(self, "sequence_scrollbar", None)
        if bar is None:
            return
        internal_bar = self.sequence_text.horizontalScrollBar()
        bar.blockSignals(True)
        bar.setRange(mn, mx)
        bar.setPageStep(internal_bar.pageStep())
        bar.setSingleStep(max(1, internal_bar.singleStep()))
        bar.setValue(internal_bar.value())
        bar.blockSignals(False)
        bar.setVisible(mx > mn)

    def _select_residue_range_by_index(self, start, end=None, additive=False):
        self._select_residue_range_for_entry(self._current_entry, start, end, additive=additive)

    def _select_residue_range_for_entry(self, entry, start, end=None, additive=False):
        if end is None:
            end = start
        if not entry or not entry["residues"]:
            return
        current_entry = entry is self._current_entry
        start = max(0, min(int(start), len(entry["residues"]) - 1))
        end = max(0, min(int(end), len(entry["residues"]) - 1))
        if end < start:
            start, end = end, start
        select_cmd = "select add" if additive else "select"
        single_click = (start == end)
        if (
            current_entry
            and single_click
            and self._selection_click_mode != "chain"
            and self._index_in_ranges(start, self._selected_ranges)
        ):
            self._toggle_off_residue(entry, start)
            return
        if self._selection_click_mode == "chain":
            chain_spec = entry["spec"]
            try:
                from chimerax.core.commands import run

                self._mark_internal_selection_update()
                run(self.session, f"{select_cmd} {chain_spec}")
                self.session._codex_bridge_last_chain_selection_spec = chain_spec
            except Exception as err:
                message = str(err) if str(err) else err.__class__.__name__
                self.session.logger.error(
                    f"[Codex chain-click] command failed: {message}"
                )
                return
            full_range = (0, len(entry["residues"]) - 1)
            if current_entry:
                self._selected_ranges = (
                    _merge_index_ranges(self._selected_ranges + [full_range])
                    if additive
                    else [full_range]
                )
                self.sequence_text.highlight_ranges(self._selected_ranges)
            self.status_label.setText(f"Selected chain {chain_spec}.")
            return
        if self._selection_click_mode == "atom":
            base_spec = self._selection_spec_for_range(entry, start, end)
            if not base_spec:
                return
            atom_spec = f"{base_spec}@CA"
            try:
                from chimerax.core.commands import run

                self._mark_internal_selection_update()
                with command_batch(self.session, f"Sequence Cα selection {atom_spec}"):
                    run(self.session, f"{select_cmd} {atom_spec}")
            except Exception as err:
                message = str(err) if str(err) else err.__class__.__name__
                self.session.logger.error(f"Atom selection failed: {message}")
                return
            if current_entry:
                self._selected_ranges = (
                    _merge_index_ranges(self._selected_ranges + [(start, end)])
                    if additive
                    else [(start, end)]
                )
                self.sequence_text.highlight_ranges(self._selected_ranges)
            self.status_label.setText(f"Selected Cα: {atom_spec}")
            return
        spec = self._selection_spec_for_range(entry, start, end)
        if not spec:
            return
        try:
            from chimerax.core.commands import run

            self._mark_internal_selection_update()
            with command_batch(self.session, f"Sequence selection {spec}"):
                run(self.session, f"{select_cmd} {spec}")
        except Exception as err:
            message = str(err) if str(err) else err.__class__.__name__
            self.session.logger.error(f"Sequence selection failed: {message}")
            return
        if current_entry:
            self._selected_ranges = (
                _merge_index_ranges(self._selected_ranges + [(start, end)])
                if additive
                else [(start, end)]
            )
            self.sequence_text.highlight_ranges(self._selected_ranges)
            ranges = self._selected_ranges
        else:
            ranges = [(start, end)]
        self.status_label.setText(
            self._status_for_ranges(entry, ranges, added=(start, end) if additive else None)
        )

    def _clear_selection_from_panel(self):
        self._selected_ranges = []
        self.sequence_text.highlight_ranges([])
        try:
            from chimerax.core.commands import run

            self._mark_internal_selection_update()
            run(self.session, "select clear")
        except Exception as err:
            message = str(err) if str(err) else err.__class__.__name__
            self.session.logger.error(f"Sequence selection clear failed: {message}")
            return
        self.status_label.setText("Selection cleared.")

    def _selection_spec_for_range(self, entry, start, end):
        residues = entry.get("residues") or []
        if start == end:
            return residues[start].get("spec")
        start_number = residues[start].get("number")
        end_number = residues[end].get("number")
        if start_number is None or end_number is None:
            return None
        return f"{entry['spec']}:{start_number}-{end_number}"

    def _index_in_ranges(self, index, ranges):
        return any(s <= index <= e for s, e in (ranges or []))

    def _remove_index_from_ranges(self, index, ranges):
        new_ranges = []
        for s, e in ranges or []:
            if index < s or index > e:
                new_ranges.append((s, e))
                continue
            if s <= index - 1:
                new_ranges.append((s, index - 1))
            if index + 1 <= e:
                new_ranges.append((index + 1, e))
        return _merge_index_ranges(new_ranges)

    def _multi_range_residue_spec(self, entry, ranges):
        if not entry or not ranges:
            return None
        residues = entry.get("residues") or []
        parts = []
        for s, e in ranges:
            if s < 0 or e >= len(residues):
                continue
            sn = residues[s].get("number")
            en = residues[e].get("number")
            if sn is None or en is None:
                continue
            parts.append(f"{sn}" if sn == en else f"{sn}-{en}")
        if not parts:
            return None
        return f"{entry['spec']}:{','.join(parts)}"

    def _toggle_off_residue(self, entry, index):
        residues = entry.get("residues") or []
        if index < 0 or index >= len(residues):
            return
        residue_spec = residues[index].get("spec")
        if not residue_spec:
            return
        deselect_target = (
            f"{residue_spec}@CA" if self._selection_click_mode == "atom" else residue_spec
        )
        try:
            from chimerax.core.commands import run

            self._mark_internal_selection_update()
            with command_batch(self.session, f"Sequence deselect {deselect_target}"):
                run(self.session, f"~select {deselect_target}")
        except Exception as err:
            message = str(err) if str(err) else err.__class__.__name__
            self.session.logger.error(f"Sequence deselect failed: {message}")
            return
        self._selected_ranges = self._remove_index_from_ranges(index, self._selected_ranges)
        self.sequence_text.highlight_ranges(self._selected_ranges)
        label = residues[index].get("label") or residue_spec
        if self._selected_ranges:
            self.status_label.setText(
                f"Removed {label} · {self._status_for_ranges(entry, self._selected_ranges)}"
            )
        else:
            self.status_label.setText(f"Removed {label} · selection cleared.")

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
            metal_text = _metal_annotation_text(
                (entry.get("metal_annotations") or [None])[start]
                if start < len(entry.get("metal_annotations") or [])
                else None,
                detailed=True,
            )
            return f"Selected {residue['label']} · {metal_text} · {spec}" if metal_text else f"Selected {residue['label']} · {spec}"
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
            selected_metal_indices = self._selected_metal_contact_indices(entry)
            return self._indices_to_ranges(selected_metal_indices)
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

    def _selected_metal_contact_indices(self, entry):
        selected_specs = set()
        try:
            from chimerax.atomic import selected_atoms

            for atom in selected_atoms(self.session):
                if not _is_metal_atom(atom):
                    continue
                selected_specs.add(_atom_atomspec(atom))
        except Exception:
            pass
        if not selected_specs:
            return []
        indices = []
        for index, annotations in enumerate(entry.get("metal_annotations") or []):
            for annotation in annotations or []:
                if annotation.get("spec") in selected_specs:
                    indices.append(index)
                    break
        return indices

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
        residue_label = residue["label"]

        # If the right-clicked residue is part of an active multi-residue drag
        # selection, retarget the residue-scoped menu items at the entire
        # selection so options apply to every selected residue.
        target_spec = residue_spec
        target_noun = "residue"
        target_status_label = residue_label
        selection_total = sum(
            e - s + 1 for s, e in (self._selected_ranges or [])
        )
        if (
            self._selected_ranges
            and selection_total > 1
            and self._index_in_ranges(index, self._selected_ranges)
        ):
            multi_spec = self._multi_range_residue_spec(entry, self._selected_ranges)
            if multi_spec:
                target_spec = multi_spec
                target_noun = f"selection ({selection_total} res)"
                target_status_label = f"{selection_total} selected residues"

        menu = QMenu(self.session.ui.main_window)
        action_menu = menu.addMenu("Action")
        action_menu.addAction(
            f"Select {target_noun}",
            lambda: self._run_commands(f"Context residue:{target_spec}", [f"select {target_spec}"]),
        )
        action_menu.addAction(
            "Select chain",
            lambda: self._run_commands(f"Context chain:{chain_spec}", [f"select {chain_spec}"]),
        )
        action_menu.addSeparator()
        action_menu.addAction(
            f"Focus {target_noun}",
            lambda: self._run_commands(f"Context focus residue:{target_spec}", [f"select {target_spec}", "view sel"]),
        )
        action_menu.addAction(
            "Focus chain",
            lambda: self._run_commands(f"Context focus chain:{chain_spec}", [f"select {chain_spec}", "view sel"]),
        )
        action_menu.addSeparator()
        action_menu.addAction(
            f"FoldDisco this {target_noun}",
            lambda spec=target_spec: self._open_folddisco(residue_spec=spec),
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
        show_menu.addAction(f"Atoms ({target_noun})", lambda: self._run_commands(f"Context show atoms:{target_spec}", [f"show {target_spec} atoms"]))
        show_menu.addAction(f"Sticks ({target_noun})", lambda: self._show_sticks(target_spec))
        show_menu.addAction(f"Cartoon ({target_noun})", lambda: self._run_commands(f"Context show cartoon:{target_spec}", [f"show {target_spec} cartoons"]))
        show_menu.addAction(f"Surface ({target_noun})", lambda: self._run_commands(f"Context surface:{target_spec}", [f"surface {target_spec}"]))
        show_menu.addSeparator()
        show_menu.addAction("Cartoon (chain)", lambda: self._run_commands(f"Context cartoon:{chain_spec}", [f"cartoon {chain_spec}"]))
        show_menu.addAction("Surface (chain)", lambda: self._run_commands(f"Context chain surface:{chain_spec}", [f"surface {chain_spec}"]))
        self._add_solvent_display_actions(show_menu, "show", model_spec)

        hide_menu = menu.addMenu("Hide")
        hide_menu.addAction(f"Atoms ({target_noun})", lambda: self._run_commands(f"Context hide atoms:{target_spec}", [f"hide {target_spec} atoms"]))
        hide_menu.addAction(f"Cartoon ({target_noun})", lambda: self._run_commands(f"Context hide cartoon:{target_spec}", [f"hide {target_spec} cartoons"]))
        hide_menu.addAction(f"Surface ({target_noun})", lambda: self._run_commands(f"Context hide surface:{target_spec}", [f"~surface {target_spec}"]))
        hide_menu.addAction(
            f"Everything ({target_noun})",
            lambda: self._run_commands(
                f"Context hide all:{target_spec}",
                [
                    f"hide {target_spec} atoms",
                    f"hide {target_spec} cartoons",
                    f"~surface {target_spec}",
                ],
            ),
        )
        hide_menu.addSeparator()
        hide_menu.addAction("Cartoon (chain)", lambda: self._run_commands(f"Context hide cartoon:{chain_spec}", [f"hide {chain_spec} cartoons"]))
        hide_menu.addAction("Surface (chain)", lambda: self._run_commands(f"Context hide chain surface:{chain_spec}", [f"~surface {chain_spec}"]))
        self._add_solvent_display_actions(hide_menu, "hide", model_spec)

        delete_menu = menu.addMenu("Delete")
        delete_menu.addAction(
            f"Delete {target_noun} atoms...",
            lambda: self._delete_target_atoms(target_spec, target_status_label),
        )
        delete_menu.addAction(
            "Delete chain atoms...",
            lambda: self._delete_target_atoms(chain_spec, chain_spec),
        )
        delete_menu.addSeparator()
        delete_menu.addAction(
            "Delete waters / solvent in model...",
            lambda checked=False, ms=model_spec: self._run_solvent_action("delete", ms),
        )
        delete_menu.addAction(
            "Delete waters / solvent in all models...",
            lambda checked=False: self._run_solvent_action("delete", None),
        )
        delete_menu.addSeparator()
        delete_menu.addAction(
            "Keep only this chain in model...",
            lambda: self._keep_only_chain(chain_spec),
        )

        color_menu = menu.addMenu("Color")
        color_menu.addAction("Carbon context + hetero elements", lambda: self._apply_context_stick_colors(target_spec))
        color_menu.addAction("By element", lambda: self._run_commands(f"Context color byelement:{target_spec}", [f"color {target_spec} byelement"]))
        color_menu.addAction("By chain", lambda: self._color_and_restore(f"Context color bychain:{chain_spec}", chain_spec, "bychain"))
        color_menu.addAction("By model", lambda: self._color_and_restore(f"Context color bymodel:{model_spec}", model_spec, "bymodel"))
        preset_menu = color_menu.addMenu("Preset")
        for color_name in ("yellow", "cyan", "magenta", "hotpink", "cornflowerblue", "orange", "gold"):
            preset_menu.addAction(
                color_name,
                lambda checked=False, c=color_name: self._color_and_restore(
                    f"Context color {c}:{target_spec}",
                    target_spec,
                    c,
                ),
            )
        color_menu.addAction("Custom...", lambda: self._pick_custom_color(target_spec))

        label_menu = menu.addMenu("Label")
        label_menu.addAction(target_noun.capitalize(), lambda: self._run_commands(f"Context label:{target_spec}", [f"label {target_spec} residues"]))
        label_menu.addAction("Chain", lambda: self._run_commands(f"Context chain label:{chain_spec}", [f"label {chain_spec} residues"]))
        label_menu.addAction("Clear labels", lambda: self._run_commands("Context label clear", ["label delete"]))

        ai_menu = menu.addMenu("AI")
        ai_menu.addAction(f"Analyze {target_noun}", lambda: self._launch_ai_prompt(f"Analyze {target_spec} with evidence and confidence.", "analyze"))
        ai_menu.addAction("Improve chain view", lambda: self._launch_ai_prompt(f"Improve the view for {chain_spec} and apply the changes directly.", "agent"))

        self.status_label.setText(f"Menu target: {target_status_label} · {target_spec}")
        self.session.ui.post_context_menu(menu, global_pos)

    def _run_commands(self, batch_label, commands):
        from chimerax.core.commands import run

        with command_batch(self.session, batch_label):
            for command in commands:
                run(self.session, command)
        self.session.logger.status(batch_label)

    def _select_metal_token(self, metal):
        spec = str((metal or {}).get("spec") or "").strip()
        if not spec:
            return
        commands = [
            f"show {spec} atoms",
            f"style {spec} sphere",
            f"select {spec}",
        ]
        self._run_commands(f"Metal select:{spec}", commands)
        self.status_label.setText(f"Selected metal {metal.get('token', '')} · {spec}")

    def _show_metal_hover(self, metal):
        if not metal:
            base = getattr(self, "_alignment_base_status", "") if self.alignment_text.isVisible() else self._sequence_base_status
            if base:
                self.status_label.setText(base)
            return
        self.status_label.setText(str(metal.get("tooltip") or metal.get("label") or metal.get("spec") or "metal"))

    def _show_metal_context_menu(self, metal, global_pos):
        spec = str((metal or {}).get("spec") or "").strip()
        if not spec:
            return
        token = str(metal.get("token") or metal.get("element") or "metal")
        menu = QMenu(self.session.ui.main_window)
        action_menu = menu.addMenu("Action")
        action_menu.addAction("Select metal", lambda: self._run_commands(f"Metal select:{spec}", [f"select {spec}"]))
        action_menu.addAction(
            "Focus metal",
            lambda: self._run_commands(
                f"Metal focus:{spec}",
                [f"show {spec} atoms", f"style {spec} sphere", f"select {spec}", "view sel"],
            ),
        )
        show_menu = menu.addMenu("Show")
        show_menu.addAction("Sphere", lambda: self._run_commands(f"Metal show sphere:{spec}", [f"show {spec} atoms", f"style {spec} sphere"]))
        show_menu.addAction("Ball", lambda: self._run_commands(f"Metal show ball:{spec}", [f"show {spec} atoms", f"style {spec} ball"]))
        hide_menu = menu.addMenu("Hide")
        hide_menu.addAction("Metal", lambda: self._run_commands(f"Metal hide:{spec}", [f"hide {spec} atoms"]))
        label_menu = menu.addMenu("Label")
        label_menu.addAction("Metal", lambda: self._run_commands(f"Metal label:{spec}", [f"label {spec} atoms"]))
        label_menu.addAction("Clear labels", lambda: self._run_commands("Metal label clear", ["label delete"]))
        color_menu = menu.addMenu("Color")
        color_menu.addAction("By element", lambda: self._run_commands(f"Metal color byelement:{spec}", [f"color {spec} byelement"]))
        for color_name in ("lime", "magenta", "cyan", "yellow", "orange"):
            color_menu.addAction(color_name, lambda checked=False, c=color_name: self._run_commands(f"Metal color {c}:{spec}", [f"color {spec} {c}"]))
        self.status_label.setText(f"Metal {token} · {spec}")
        self.session.ui.post_context_menu(menu, global_pos)

    def _add_solvent_display_actions(self, menu, action, model_spec=None):
        menu.addSeparator()
        if model_spec:
            menu.addAction(
                f"Waters / solvent (model {model_spec})",
                lambda checked=False, ms=model_spec, a=action: self._run_solvent_action(a, ms),
            )
        menu.addAction(
            "Waters / solvent (all models)",
            lambda checked=False, a=action: self._run_solvent_action(a, None),
        )

    def _confirm_destructive(self, title, message):
        try:
            from Qt.QtWidgets import QMessageBox

            reply = QMessageBox.question(
                self.tool_window.ui_area,
                title,
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            return reply == QMessageBox.StandardButton.Yes
        except Exception:
            return True

    def _delete_target_atoms(self, spec, label):
        if not self._confirm_destructive(
            "Delete atoms",
            f"Delete atoms for {label}?\n\nThis removes them from the model, not just from the display.",
        ):
            return
        try:
            from .chain_cleanup import delete_atomspec

            message = delete_atomspec(self.session, spec)
        except Exception as err:
            self.status_label.setText(str(err) if str(err) else err.__class__.__name__)
            return
        self.status_label.setText(message)
        self.session.logger.status(message)
        self.refresh()

    def _keep_only_chain(self, chain_spec):
        if not self._confirm_destructive(
            "Keep only this chain",
            f"Keep {chain_spec} and delete every other chain in the same model?\n\nThis removes atoms from the model.",
        ):
            return
        try:
            from .chain_cleanup import keep_only_chain

            message = keep_only_chain(self.session, chain_spec)
        except Exception as err:
            self.status_label.setText(str(err) if str(err) else err.__class__.__name__)
            return
        self.status_label.setText(message)
        self.session.logger.status(message)
        self.refresh()

    def _run_solvent_action(self, action, model_spec=None):
        if action == "delete":
            scope = model_spec or "all models"
            if not self._confirm_destructive(
                "Delete waters / solvent",
                f"Delete waters / solvent in {scope}?\n\nThis removes atoms from the model, not just from the display.",
            ):
                return
        try:
            from .chain_cleanup import delete_solvent, hide_solvent, show_solvent

            functions = {
                "delete": delete_solvent,
                "hide": hide_solvent,
                "show": show_solvent,
            }
            message = functions[action](self.session, model_spec=model_spec)
        except Exception as err:
            self.status_label.setText(str(err) if str(err) else err.__class__.__name__)
            return
        self.status_label.setText(message)
        self.session.logger.status(message)
        self.refresh()

    def _color_and_restore(self, batch_label, spec, color):
        from chimerax.core.commands import run

        with command_batch(self.session, batch_label):
            run(self.session, f"color {spec} {color}")
            restore_charge_colors(self.session, spec)
            self._auto_name_region(spec, color)
        self.session.logger.status(batch_label)

    def _auto_name_region(self, spec, color):
        s = str(spec or "").strip()
        if not s or s == "sel":
            return
        if ":" not in s:
            return
        if color in ("bychain", "bymodel", "byelement", "byhetero", "byidentity"):
            return
        name = "codex_" + "".join(
            ch if (ch.isalnum() or ch == "_") else "_" for ch in s
        ).strip("_")
        if not name or name == "codex_":
            return
        try:
            from chimerax.core.commands import run

            run(self.session, f"name {name} {s}")
            from .named_selection import add_group

            add_group(self.session, name, s)
        except Exception as err:
            self.session.logger.info(
                f"[Codex auto-name] skipped {s!r}: {err}"
            )

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

        try:
            from .display_color import show_sticks_with_cartoon_anchor
        except Exception as err:
            show_sticks_with_cartoon_anchor = None
            self.session.logger.warning(f"Could not load stick display helper: {err}")

        with command_batch(self.session, f"Context sticks:{residue_spec}"):
            if show_sticks_with_cartoon_anchor is not None:
                try:
                    show_sticks_with_cartoon_anchor(self.session, residue_spec)
                except Exception as err:
                    self.session.logger.warning(f"Could not show connected sticks for {residue_spec}: {err}")
                    run(self.session, f"show {residue_spec} atoms")
                    run(self.session, f"style {residue_spec} stick")
            else:
                run(self.session, f"show {residue_spec} atoms")
                run(self.session, f"style {residue_spec} stick")
        self.session.logger.status(f"Context sticks:{residue_spec}")

    def _apply_context_stick_colors(self, residue_spec):
        try:
            from .display_color import apply_stick_context_colors
        except Exception as err:
            self.session.logger.warning(f"Could not load stick color restorer: {err}")
            return

        with command_batch(self.session, f"Context stick colors:{residue_spec}"):
            try:
                apply_stick_context_colors(self.session, residue_spec)
            except Exception as err:
                self.session.logger.warning(f"Could not restore stick context colors for {residue_spec}: {err}")
                return
        self.session.logger.status(f"Context stick colors:{residue_spec}")

    def _pick_custom_color(self, spec):
        from Qt.QtGui import QColor
        from Qt.QtWidgets import QColorDialog

        color = QColorDialog.getColor(QColor("#ffd166"), self.session.ui.main_window, "Choose ChimeraX Color")
        if not color.isValid():
            return
        self._color_and_restore(f"Context color custom:{spec}", spec, color.name())

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

    def _render_alignment_panel(self, entry):
        payload = _alignment_payload_for_entry(self.session, entry)
        if not payload:
            self._alignment_payload = None
            self._alignment_base_status = ""
            self._alignment_selected_columns = {}
            self.alignment_text.set_alignment_text("", 0)
            self.alignment_text.setVisible(False)
            self.alignment_text.setFixedHeight(0)
            self.sequence_text.setVisible(True)
            if hasattr(self, "search_edit"):
                self.search_edit.setEnabled(True)
                self.search_edit.setPlaceholderText("Find seq (X = any)…")
            self._refresh_search_highlights()
            return

        self._alignment_payload = payload
        self._alignment_selected_columns = {}
        text, height = _format_alignment_panel(payload)
        alignment_styles = {
            row_name: side.get("styles")
            for row_name, side in _alignment_payload_row_items(payload)
        }
        self.alignment_text.set_alignment_text(
            text,
            payload.get("length", 0),
            alignment_styles,
            payload.get("display_layout"),
            payload.get("display_tooltips"),
        )
        selected_columns = self._refresh_alignment_selection_highlights(payload, scroll=True, update_status=False)
        self.alignment_text.setFixedHeight(height)
        self.alignment_text.setVisible(True)
        self.sequence_text.setVisible(False)
        if hasattr(self, "search_edit"):
            self.search_edit.setEnabled(True)
            self.search_edit.setPlaceholderText("Find alignment (X = any)…")
        self._refresh_search_highlights()
        identity_pct = payload.get("identity", 0.0) * 100.0
        ref = payload.get("reference", {})
        mov = payload.get("moving", {})
        rows = _alignment_payload_rows(payload)
        if payload.get("multi_alignment"):
            source = str(payload.get("alignment_source") or "alignment").replace("_", "-")
            mode = f"{len(rows)}-row {source}"
            status = (
                f"{entry['spec']} · {entry['length']} residues · {mode} · "
                f"avg {identity_pct:.1f}% identity vs {ref.get('display', 'reference')}"
            )
        elif payload.get("alignment_source") == "scene_position":
            mode = f"structure-position pair ({int(payload.get('matched_pairs', 0) or 0)} matched)"
            status = (
                f"{entry['spec']} · {entry['length']} residues · {mode} {identity_pct:.1f}% identity · "
                f"{ref.get('display', 'reference')} vs {mov.get('display', 'moving')}"
            )
        else:
            mode = "auto pairwise" if payload.get("auto_pairwise") else "aligned pair"
            status = (
                f"{entry['spec']} · {entry['length']} residues · {mode} {identity_pct:.1f}% identity · "
                f"{ref.get('display', 'reference')} vs {mov.get('display', 'moving')}"
            )
        self._alignment_base_status = status
        selected_status = self._alignment_selected_status(payload, selected_columns)
        self.status_label.setText(selected_status or status)

    def _show_alignment_hover(self, column):
        if column is None:
            base_status = getattr(self, "_alignment_base_status", "")
            if base_status:
                self.status_label.setText(base_status)
            return
        payload = getattr(self, "_alignment_payload", None) or {}
        statuses = list(payload.get("display_statuses") or [])
        if 0 <= int(column) < len(statuses):
            self.status_label.setText(statuses[int(column)])

    def _select_alignment_range_by_column(self, start_column, end_column=None, row="reference"):
        payload = getattr(self, "_alignment_payload", None)
        if not payload:
            return
        if end_column is None:
            end_column = start_column
        start_column = int(start_column)
        end_column = int(end_column)
        if end_column < start_column:
            start_column, end_column = end_column, start_column

        side = _alignment_side_for_row(payload, row)
        column_map = side.get("column_map")
        if not column_map:
            # No residue mapping was registered for this alignment side.
            # The previous fallback called an undefined helper which raised
            # NameError; without atomic-structure context the mapping cannot
            # be reconstructed from the aligned text alone, so surface the
            # warning instead.
            self.status_label.setText("Alignment column has no residue mapping.")
            return

        upper = min(end_column, len(column_map) - 1)
        atomspecs = []
        positions = []
        for column in range(max(0, start_column), upper + 1):
            entry = column_map[column]
            if entry is None:
                continue
            if isinstance(entry, str) and entry.startswith("#"):
                atomspecs.append(entry)
            else:
                positions.append(entry)
        if not atomspecs and not positions:
            self.status_label.setText("Alignment column is a gap; no residue to select.")
            return

        if atomspecs:
            try:
                from chimerax.core.commands import run as _run
                spec = " ".join(atomspecs)
                self._mark_internal_selection_update()
                _run(self.session, f"select {spec}")
                self.status_label.setText(f"Selected {len(atomspecs)} aligned residue(s) on {row} chain.")
            except Exception as err:
                self.status_label.setText(f"Selection failed: {err}")
            return

        target_entry = self._entry_for_alignment_row(payload, row)
        if target_entry is None:
            self.status_label.setText("Could not resolve the chain for this alignment row.")
            return
        self._select_residue_range_for_entry(target_entry, min(positions), max(positions))

    def _entry_for_alignment_row(self, payload, row):
        side = _alignment_side_for_row(payload, row)
        spec = str(side.get("spec") or "")
        if spec:
            for entry in self._entries:
                if entry.get("spec") == spec:
                    return entry
        if row == "reference":
            return self._entry_for_alignment_reference(payload)
        if row == "moving":
            return self._entry_for_alignment_side(payload, "moving")
        return None

    def _entry_for_alignment_side(self, payload, side_name):
        side = payload.get(side_name, {}) if payload else {}
        spec = str(side.get("spec") or "")
        if spec:
            for entry in self._entries:
                if entry.get("spec") == spec:
                    return entry
        return None

    def _entry_for_alignment_reference(self, payload):
        ref = payload.get("reference", {})
        ref_spec = str(ref.get("spec") or "")
        if ref_spec:
            for entry in self._entries:
                if entry.get("spec") == ref_spec:
                    return entry
        ref_entry = ref.get("entry")
        if ref_entry:
            return ref_entry
        entry = self._current_entry
        if entry and (not ref_spec or entry.get("spec") == ref_spec):
            return entry
        return None


def _protein_sequence_entries(session):
    from chimerax.atomic import AtomicStructure, Residue

    entries = []
    for model in session.models.list(type=AtomicStructure):
        model_displayed = bool(getattr(model, "display", True))
        if not model_displayed:
            continue
        model_spec = f"#{getattr(model, 'id_string', '?')}"
        model_name = getattr(model, "name", "structure")
        model_selected = bool(getattr(model, "selected", False))
        for chain in getattr(model, "chains", []):
            polymer_type = getattr(chain, "polymer_type", None)
            if polymer_type not in (Residue.PT_AMINO, Residue.PT_PROTEIN):
                continue
            entry = _entry_for_chain(model_spec, model_name, chain)
            if entry:
                entry["displayed"] = True
                entry["selected"] = model_selected
                entries.append(entry)
    entries.sort(key=lambda item: (item["model_spec"], item["chain_id"]))
    return entries


def _selected_chain_specs_from_session(session):
    specs = set()
    try:
        from chimerax.atomic import selected_residues

        selected = selected_residues(session)
        for residue in selected:
            structure = getattr(residue, "structure", None)
            model_id = getattr(structure, "id_string", None)
            chain_id = str(getattr(residue, "chain_id", "") or "").strip()
            if model_id and chain_id:
                specs.add(f"#{model_id}/{chain_id}")
    except Exception:
        pass
    if specs:
        return specs
    try:
        from chimerax.atomic import selected_atoms

        for atom in selected_atoms(session):
            residue = getattr(atom, "residue", None)
            structure = getattr(residue, "structure", None)
            model_id = getattr(structure, "id_string", None)
            chain_id = str(getattr(residue, "chain_id", "") or "").strip()
            if model_id and chain_id:
                specs.add(f"#{model_id}/{chain_id}")
    except Exception:
        pass
    return specs


def _selected_residue_keys_from_session(session):
    keys = set()
    try:
        from chimerax.atomic import selected_residues

        for residue in selected_residues(session):
            keys.update(_residue_object_keys(residue))
    except Exception:
        pass
    if keys:
        return keys
    try:
        from chimerax.atomic import selected_atoms

        for atom in selected_atoms(session):
            keys.update(_residue_object_keys(getattr(atom, "residue", None)))
    except Exception:
        pass
    return keys


def _residue_object_keys(residue):
    keys = set()
    if residue is None:
        return keys
    spec = str(getattr(residue, "atomspec", "") or "").strip()
    if spec:
        keys.add(spec)
        keys.add(_canonical_residue_key_from_spec(spec))
    structure = getattr(residue, "structure", None)
    model_id = getattr(structure, "id_string", None)
    chain_id = str(getattr(residue, "chain_id", "") or "").strip()
    number = str(getattr(residue, "number", "") or "").strip()
    if model_id and chain_id and number:
        keys.add(f"#{model_id}/{chain_id}:{number}")
        keys.add(f"#{model_id}|{chain_id}|{number}")
    return {key for key in keys if key}


def _alignment_payload_for_session(session):
    payload = getattr(session, "_codex_bridge_last_structure_alignment_payload", None)
    if payload:
        return payload
    manager = getattr(session, "alignments", None)
    if manager is None:
        return None
    alignments = list(getattr(manager, "alignments", []) or [])
    if not alignments:
        return None
    last_id = getattr(session, "_codex_bridge_last_alignment_id", None)
    target = None
    if last_id:
        for alignment in alignments:
            if getattr(alignment, "ident", None) == last_id:
                target = alignment
                break
    if target is None:
        target = alignments[-1]
    return _alignment_to_payload(target)


def _column_map_from_chain(chain, aligned_text):
    """Map alignment column index → residue atomspec for a real ChimeraX chain.

    Honours insertion codes, missing residues, and non-positive numbering by
    pulling each residue's `.atomspec`. Critic P1 #9: lowercase letters (Foldseek
    = unaligned insertion) consume a residue too, but the column itself is not
    clickable. So we advance residue_index for any letter, and only return None
    for gaps (`-`) and lowercase columns.
    """
    structure = getattr(chain, "structure", None)
    model_prefix = (
        f"#{structure.id_string}" if structure is not None and getattr(structure, "id_string", None) else ""
    )
    residues = list(getattr(chain, "existing_residues", []) or [])
    column_map = []
    residue_index = 0
    for ch in str(aligned_text or ""):
        if _is_alignment_gap(ch):
            column_map.append(None)
            continue
        is_insertion = ch.islower()
        spec = ""
        if residue_index < len(residues):
            residue = residues[residue_index]
            raw_spec = getattr(residue, "atomspec", "") or ""
            if raw_spec.startswith("#"):
                spec = raw_spec
            elif raw_spec:
                spec = f"{model_prefix}{raw_spec}" if model_prefix else raw_spec
            residue_index += 1
        column_map.append(None if is_insertion else (spec or None))
    return column_map


def _column_labels_from_chain(chain, aligned_text):
    if chain is None:
        return None
    residues = list(getattr(chain, "existing_residues", []) or [])
    chain_id = str(getattr(chain, "chain_id", "") or "?").strip() or "?"
    labels = []
    residue_index = 0
    for ch in str(aligned_text or ""):
        if _is_alignment_gap(ch):
            labels.append(None)
            continue
        label = None
        if residue_index < len(residues):
            residue = residues[residue_index]
            if not ch.islower():
                label = _residue_object_alignment_label(residue, chain_id=chain_id)
            residue_index += 1
        labels.append(label)
    return labels


def _aligned_styles_from_chain(chain, aligned_text):
    if chain is None:
        return None
    residues = list(getattr(chain, "existing_residues", []) or [])
    styles = []
    residue_index = 0
    for ch in str(aligned_text or ""):
        if _is_alignment_gap(ch):
            styles.append(None)
            continue
        style = None
        if residue_index < len(residues):
            style = _residue_display_style(residues[residue_index])
            residue_index += 1
        styles.append(style)
    return styles


def _aligned_metal_annotations_from_chain(chain, aligned_text):
    if chain is None:
        return None
    annotations = _metal_annotations_for_chain(chain)
    mapped = []
    residue_index = 0
    for ch in str(aligned_text or ""):
        if _is_alignment_gap(ch):
            mapped.append(None)
            continue
        item = None
        if residue_index < len(annotations):
            item = annotations[residue_index]
            residue_index += 1
        mapped.append(item if item else None)
    return mapped


def _seq_chain(seq):
    """Best-effort lookup of the ChimeraX Chain backing an alignment Sequence."""
    chain = getattr(seq, "chain", None)
    if chain is not None:
        return chain
    match_maps = getattr(seq, "match_maps", None) or {}
    for chain in match_maps:
        return chain
    return None


def _seq_atomspec_label(seq):
    """Stable display label that includes the model spec (so #1/A vs #2/A is obvious)."""
    chain = _seq_chain(seq)
    if chain is not None:
        structure = getattr(chain, "structure", None)
        sid = getattr(structure, "id_string", None) or "?"
        cid = getattr(chain, "chain_id", None) or "?"
        sname = getattr(structure, "name", "") if structure is not None else ""
        return f"#{sid}/{cid}{(' · ' + sname) if sname else ''}"
    return getattr(seq, "name", "sequence")


def _alignment_to_payload(alignment, target_entry=None):
    seqs = list(getattr(alignment, "seqs", []) or [])
    if len(seqs) < 2:
        return None
    if len(seqs) > 2:
        return _alignment_to_multi_payload(alignment, target_entry=target_entry)
    ref_seq, mov_seq = _alignment_pair_for_target(seqs, target_entry)
    ref_aligned = str(getattr(ref_seq, "characters", ""))
    mov_aligned = str(getattr(mov_seq, "characters", ""))
    ref_chain = _seq_chain(ref_seq)
    mov_chain = _seq_chain(mov_seq)
    ref_struct = getattr(ref_chain, "structure", None) if ref_chain is not None else None
    mov_struct = getattr(mov_chain, "structure", None) if mov_chain is not None else None
    ref_spec = (
        f"#{ref_struct.id_string}/{getattr(ref_chain, 'chain_id', '?')}"
        if ref_chain is not None and ref_struct is not None else ""
    )
    mov_spec = (
        f"#{mov_struct.id_string}/{getattr(mov_chain, 'chain_id', '?')}"
        if mov_chain is not None and mov_struct is not None else ""
    )
    return {
        "alignment_id": getattr(alignment, "ident", None),
        "name": getattr(alignment, "description", "") or "Alignment",
            "reference": {
                "display": _seq_atomspec_label(ref_seq),
                "aligned": ref_aligned,
                "spec": ref_spec,
                "_structure": ref_struct,
                "column_map": _column_map_from_chain(ref_chain, ref_aligned) if ref_chain is not None else None,
                "column_labels": _column_labels_from_chain(ref_chain, ref_aligned),
                "styles": _aligned_styles_from_chain(ref_chain, ref_aligned),
                "metal_annotations": _aligned_metal_annotations_from_chain(ref_chain, ref_aligned),
            },
            "moving": {
                "display": _seq_atomspec_label(mov_seq),
                "aligned": mov_aligned,
                "spec": mov_spec,
                "_structure": mov_struct,
                "column_map": _column_map_from_chain(mov_chain, mov_aligned) if mov_chain is not None else None,
                "column_labels": _column_labels_from_chain(mov_chain, mov_aligned),
                "styles": _aligned_styles_from_chain(mov_chain, mov_aligned),
                "metal_annotations": _aligned_metal_annotations_from_chain(mov_chain, mov_aligned),
            },
        "length": len(ref_aligned),
        "identity": _aligned_identity(ref_aligned, mov_aligned),
    }


def _alignment_pair_for_target(seqs, target_entry=None):
    if not target_entry:
        return seqs[0], seqs[1]
    target_index = None
    for index, seq in enumerate(seqs):
        if _seq_matches_entry(seq, target_entry):
            target_index = index
            break
    if target_index is None:
        return seqs[0], seqs[1]
    reference_hint = str(target_entry.get("reference_spec") or "").strip()
    reference_index = None
    if reference_hint:
        for index, seq in enumerate(seqs):
            chain = _seq_chain(seq)
            if chain is None:
                continue
            structure = getattr(chain, "structure", None)
            spec = (
                f"#{structure.id_string}/{getattr(chain, 'chain_id', '?')}"
                if structure is not None and getattr(structure, "id_string", None)
                else ""
            )
            if spec == reference_hint:
                reference_index = index
                break
    if reference_index is None:
        reference_index = 0
    if reference_index == target_index:
        for index in range(len(seqs)):
            if index != target_index:
                return seqs[target_index], seqs[index]
    return seqs[reference_index], seqs[target_index]


def _seq_matches_entry(seq, entry):
    chain = _seq_chain(seq)
    if chain is None or not entry:
        return False
    structure = getattr(chain, "structure", None)
    model_id = getattr(structure, "id_string", None) if structure is not None else None
    chain_id = str(getattr(chain, "chain_id", "") or "").strip()
    return (
        model_id == str(entry.get("model_spec") or "").lstrip("#")
        and (not entry.get("chain_id") or chain_id == str(entry.get("chain_id")))
    )


def _alignment_to_multi_payload(alignment, target_entry=None):
    seqs = _ordered_alignment_sequences_for_target(
        list(getattr(alignment, "seqs", []) or []),
        target_entry,
    )
    if len(seqs) < 3:
        return None
    rows = []
    for index, seq in enumerate(seqs):
        row_id = "reference" if index == 0 else ("moving" if index == 1 else f"row{index}")
        aligned = str(getattr(seq, "characters", ""))
        chain = _seq_chain(seq)
        structure = getattr(chain, "structure", None) if chain is not None else None
        spec = (
            f"#{structure.id_string}/{getattr(chain, 'chain_id', '?')}"
            if chain is not None and structure is not None else ""
        )
        rows.append(
            {
                "row_id": row_id,
                "display": _seq_atomspec_label(seq),
                "aligned": aligned,
                "spec": spec,
                "_structure": structure,
                "column_map": _column_map_from_chain(chain, aligned) if chain is not None else None,
                "column_labels": _column_labels_from_chain(chain, aligned),
                "styles": _aligned_styles_from_chain(chain, aligned),
                "metal_annotations": _aligned_metal_annotations_from_chain(chain, aligned),
            }
        )
    length = min((len(str(row.get("aligned") or "")) for row in rows), default=0)
    if length <= 0:
        return None
    for row in rows:
        for key in ("aligned", "column_map", "column_labels", "styles"):
            value = row.get(key)
            if isinstance(value, str):
                row[key] = value[:length]
            elif value is not None:
                row[key] = list(value)[:length]
    payload = {
        "alignment_id": getattr(alignment, "ident", None),
        "name": getattr(alignment, "description", "") or "Multiple alignment",
        "multi_alignment": True,
        "alignment_source": "chimerax_alignment",
        "rows": rows,
        "reference": rows[0],
        "moving": rows[1],
        "length": length,
        "identity": _average_identity_vs_reference(rows),
        "pair_count": max(0, len(rows) - 1),
    }
    return payload


def _ordered_alignment_sequences_for_target(seqs, target_entry=None):
    if len(seqs) < 2:
        return seqs
    ref_seq, mov_seq = _alignment_pair_for_target(seqs, target_entry)
    ordered = []
    for seq in (ref_seq, mov_seq):
        if seq is not None and seq not in ordered:
            ordered.append(seq)
    for seq in seqs:
        if seq not in ordered:
            ordered.append(seq)
    return ordered


def _alignment_payload_rows(payload):
    if not isinstance(payload, dict):
        return []
    rows = list(payload.get("rows") or [])
    if rows:
        return rows
    result = []
    for row_id in ("reference", "moving"):
        side = payload.get(row_id)
        if isinstance(side, dict) and side:
            row = dict(side)
            row.setdefault("row_id", row_id)
            result.append(row)
    return result


def _alignment_payload_row_items(payload):
    items = []
    for index, row in enumerate(_alignment_payload_rows(payload)):
        row_id = str(
            row.get("row_id")
            or ("reference" if index == 0 else ("moving" if index == 1 else f"row{index}"))
        )
        items.append((row_id, row))
    return items


def _alignment_side_for_row(payload, row):
    row = str(row or "reference")
    for row_id, side in _alignment_payload_row_items(payload):
        if row_id == row:
            return side
    if isinstance(payload, dict):
        side = payload.get(row)
        if isinstance(side, dict):
            return side
    return {}


def _alignment_payload_for_entry(session, entry):
    """Pick the alignment that actually involves the given chain entry.

    Lookup priority:
      1. Current scene-position pair, when the displayed structures have enough
         close principal-atom pairs. This reflects a manual MatchMaker fit even
         when ChimeraX did not create a persistent alignment object.
      2. Explicit registry `session._codex_bridge_hit_alignments` (toolbar
         writes this when a Foldseek/native alignment opens) keyed by the
         entry's chain spec — this is the only reliable map for multi-hit runs.
      3. Search `session.alignments.alignments` for one whose seqs are bound
         (via match_maps) to the entry's chain.
      4. Cached `_codex_bridge_last_structure_alignment_payload` IF its ref or
         moving spec matches the entry. (No `alignments[-1]` blind fallback —
         that causes the panel to show the wrong pair after multiple hits.)
      5. Weak auto-pair fallback/None — caller falls back to the standalone
         sequence view if no pair is unambiguous.
    """
    if entry is None:
        return _alignment_payload_for_session(session)

    entry_spec = str(entry.get("spec") or "")
    model_spec = str(entry.get("model_spec") or "")
    auto_pairwise = globals().get("_auto_pairwise_alignment_payload_for_entry")
    scene_payload = auto_pairwise(session, entry) if auto_pairwise is not None else None
    if scene_payload is not None and (
        scene_payload.get("multi_alignment")
        or int(scene_payload.get("matched_pairs", 0) or 0) >= 3
    ):
        return scene_payload

    registry = getattr(session, "_codex_bridge_hit_alignments", None) or {}
    for key in (entry_spec, model_spec):
        if key and key in registry:
            return registry[key]

    target_chain_id = entry.get("chain_id")
    target_model_spec = entry.get("model_spec") or ""
    target_model_id = target_model_spec.lstrip("#")

    manager = getattr(session, "alignments", None)
    candidates = []
    if manager is not None:
        candidates = list(getattr(manager, "alignments", []) or [])

    for alignment in candidates:
        seqs = list(getattr(alignment, "seqs", []) or [])
        if len(seqs) < 2:
            continue
        for seq in seqs:
            chain = _seq_chain(seq)
            if chain is None:
                continue
            structure = getattr(chain, "structure", None)
            if structure is None:
                continue
            if (
                getattr(structure, "id_string", None) == target_model_id
                and (target_chain_id in (None, "", "?") or getattr(chain, "chain_id", None) == target_chain_id)
            ):
                return _alignment_to_payload(alignment, target_entry=entry)

    cached = getattr(session, "_codex_bridge_last_structure_alignment_payload", None)
    if cached:
        cached_ref_spec = (cached.get("reference") or {}).get("spec") or ""
        cached_mov_spec = (cached.get("moving") or {}).get("spec") or ""
        if entry_spec and entry_spec in (cached_ref_spec, cached_mov_spec):
            return cached
    return scene_payload


def _auto_pairwise_alignment_payload_for_entry(session, entry):
    """Fallback alignment view for the common PyMOL-like case:
    exactly two displayed protein chains, a reference-vs-current pair, or the
    best current-vs-neighbor pair when several displayed models share a chain.
    If MatchMaker created a real ChimeraX alignment we use that first; this is
    only for the default showAlignment=false path.
    """
    if entry is None:
        return None
    try:
        entries = _protein_sequence_entries(session)
    except Exception:
        return None
    if len(entries) < 2:
        return None

    entry_spec = str(entry.get("spec") or "")
    current = next((item for item in entries if item.get("spec") == entry_spec), entry)
    if len(entries) >= 3:
        multi_payload = _auto_multi_alignment_payload_for_entry(session, current, entries=entries)
        if multi_payload is not None:
            return multi_payload
    paired = None
    reference_entry = _alignment_reference_entry(session, entries, current)
    if reference_entry is not None and reference_entry.get("spec") != current.get("spec"):
        paired = (reference_entry, current)

    if paired is not None:
        pass
    elif len(entries) == 2:
        if entries[0].get("spec") == current.get("spec"):
            paired = (entries[0], entries[1])
        elif entries[1].get("spec") == current.get("spec"):
            paired = (entries[1], entries[0])
        else:
            paired = (entries[0], entries[1])
    else:
        other_model_entries = [
            item
            for item in entries
            if item.get("model_spec") != current.get("model_spec")
        ]
        same_chain = [
            item
            for item in other_model_entries
            if item.get("chain_id") == current.get("chain_id")
        ]
        if len(same_chain) == 1:
            paired = (current, same_chain[0])
        elif same_chain:
            best_payload = _best_entry_pair_alignment_payload(current, same_chain)
            if best_payload is not None:
                return best_payload
        else:
            best_entry = _best_entry_alignment_candidate(current, other_model_entries)
            if best_entry is not None:
                paired = (current, best_entry)
            else:
                by_model = {}
                for item in entries:
                    by_model.setdefault(item.get("model_spec"), []).append(item)
                if (
                    current.get("model_spec") in by_model
                    and len(by_model) == 2
                    and len(by_model[current.get("model_spec")]) == 1
                ):
                    other_groups = [
                        group
                        for model_spec, group in by_model.items()
                        if model_spec != current.get("model_spec")
                    ]
                    if len(other_groups) == 1 and len(other_groups[0]) == 1:
                        paired = (current, other_groups[0][0])
                    else:
                        one_per_other_model = [
                            group[0]
                            for model_spec, group in by_model.items()
                            if model_spec != current.get("model_spec") and len(group) == 1
                        ]
                        best_payload = _best_entry_pair_alignment_payload(current, one_per_other_model)
                        if best_payload is not None:
                            return best_payload

    if not paired:
        return None
    reference, moving = paired
    if reference.get("spec") == moving.get("spec"):
        return None
    try:
        return _entry_pair_alignment_payload(reference, moving)
    except Exception:
        return None


def _auto_multi_alignment_payload_for_entry(session, entry, entries=None):
    entries = list(entries or _protein_sequence_entries(session))
    if len(entries) < 3 or entry is None:
        return None
    current = next(
        (item for item in entries if item.get("spec") == entry.get("spec")),
        entry,
    )
    reference = _alignment_reference_entry(session, entries, current) or current
    align_entries = _multi_alignment_entries(reference, current, entries)
    if len(align_entries) < 3:
        return None
    try:
        return _entries_multi_alignment_payload(reference, align_entries)
    except Exception as err:
        try:
            session.logger.warning(
                f"Codex sequence multi-alignment fallback failed: {err.__class__.__name__}: {err}"
            )
        except Exception:
            pass
        return None


def _alignment_reference_entry(session, entries, current):
    reference_hint = str(getattr(session, "_codex_bridge_last_alignment_reference_chain_spec", "") or "").strip()
    reference_model_hint = str(getattr(session, "_codex_bridge_last_alignment_reference_spec", "") or "").strip()
    reference_entry = None
    if reference_hint:
        reference_entry = next((item for item in entries if item.get("spec") == reference_hint), None)
    if reference_entry is None and reference_model_hint:
        reference_entry = next((item for item in entries if item.get("model_spec") == reference_model_hint), None)
    return reference_entry or current


def _multi_alignment_entries(reference, current, entries):
    selected = []

    def add(entry):
        if not entry:
            return
        spec = entry.get("spec")
        if not spec or any(item.get("spec") == spec for item in selected):
            return
        selected.append(entry)

    add(reference)
    other_model_entries = [
        item
        for item in entries
        if item.get("spec") != reference.get("spec")
        and item.get("model_spec") != reference.get("model_spec")
    ]
    same_chain = [
        item
        for item in other_model_entries
        if item.get("chain_id") == reference.get("chain_id")
    ]
    if same_chain:
        for item in same_chain:
            add(item)
    else:
        by_model = {}
        for item in other_model_entries:
            by_model.setdefault(item.get("model_spec"), []).append(item)
        for model_spec in sorted(by_model):
            group = by_model[model_spec]
            add(_best_entry_alignment_candidate(reference, group))
    if current is not None and current.get("model_spec") != reference.get("model_spec"):
        add(current)
    return selected


def _best_entry_alignment_candidate(reference, candidates):
    best_entry = None
    best_score = None
    for moving in candidates or []:
        if not moving or reference.get("spec") == moving.get("spec"):
            continue
        try:
            payload = _entry_pair_alignment_payload(reference, moving)
        except Exception:
            continue
        score = _entry_pair_alignment_score(payload, reference, moving)
        if best_score is None or score > best_score:
            best_entry = moving
            best_score = score
    return best_entry


def _best_entry_pair_alignment_payload(reference, candidates):
    best_payload = None
    best_score = None
    for moving in candidates or []:
        if not moving or reference.get("spec") == moving.get("spec"):
            continue
        try:
            payload = _entry_pair_alignment_payload(reference, moving)
        except Exception:
            continue
        score = _entry_pair_alignment_score(payload, reference, moving)
        if best_score is None or score > best_score:
            best_payload = payload
            best_score = score
    return best_payload


def _entry_pair_alignment_score(payload, reference, moving):
    if payload is None:
        return (-1, -1, -1.0, -1, -1)
    source_bonus = 1 if payload.get("alignment_source") == "scene_position" else 0
    try:
        matched_pairs = int(payload.get("matched_pairs", 0) or 0)
    except Exception:
        matched_pairs = 0
    try:
        identity = float(payload.get("identity", 0.0) or 0.0)
    except Exception:
        identity = 0.0
    try:
        length = int(payload.get("length", 0) or 0)
    except Exception:
        length = 0
    same_chain_bonus = 1 if reference.get("chain_id") == moving.get("chain_id") else 0
    return (source_bonus, matched_pairs, same_chain_bonus, identity, length)


def _entries_multi_alignment_payload(reference, entries):
    ref_len = int(reference.get("length", 0) or len(reference.get("sequence", "") or ""))
    if ref_len <= 0 or len(entries) < 3:
        return None
    pair_infos = []
    source_counts = {}
    total_matched = 0
    for moving in entries[1:]:
        data = _entry_pair_alignment_indices(reference, moving)
        source = data.get("source") or "sequence"
        source_counts[source] = source_counts.get(source, 0) + 1
        total_matched += int(data.get("matched_pairs", 0) or 0)
        pair_infos.append((moving, data, _pair_alignment_star_index(data, ref_len)))

    insert_widths = [0] * (ref_len + 1)
    for _moving, _data, star in pair_infos:
        insertions = star["insertions"]
        for slot in range(ref_len + 1):
            insert_widths[slot] = max(insert_widths[slot], len(insertions[slot]))

    ref_index_map = []
    moving_index_maps = {moving.get("spec"): [] for moving, _data, _star in pair_infos}
    for slot in range(ref_len + 1):
        width = insert_widths[slot]
        for offset in range(width):
            ref_index_map.append(None)
            for moving, _data, star in pair_infos:
                insertions = star["insertions"][slot]
                moving_index_maps[moving.get("spec")].append(insertions[offset] if offset < len(insertions) else None)
        if slot < ref_len:
            ref_index_map.append(slot)
            for moving, _data, star in pair_infos:
                moving_index_maps[moving.get("spec")].append(star["by_ref"][slot])

    rows = [_entry_alignment_side_from_index_map(reference, "reference", ref_index_map)]
    for index, (moving, _data, _star) in enumerate(pair_infos, start=1):
        row_id = "moving" if index == 1 else f"row{index}"
        rows.append(
            _entry_alignment_side_from_index_map(
                moving,
                row_id,
                moving_index_maps.get(moving.get("spec"), []),
            )
        )
    source = "mixed"
    if len(source_counts) == 1:
        source = next(iter(source_counts))
    payload = {
        "alignment_id": "codex_auto_multi_" + str(reference.get("spec", "ref")).replace("#", "").replace("/", "_"),
        "name": "Auto multiple structure alignment",
        "multi_alignment": True,
        "alignment_source": source,
        "matched_pairs": total_matched,
        "pair_count": len(rows) - 1,
        "reference": rows[0],
        "moving": rows[1],
        "rows": rows,
        "length": len(rows[0].get("aligned", "")),
        "identity": _average_identity_vs_reference(rows),
    }
    return payload


def _pair_alignment_star_index(data, ref_len):
    insertions = [[] for _ in range(ref_len + 1)]
    by_ref = [None] * ref_len
    last_ref = -1
    ref_map = list(data.get("ref_map") or [])
    mov_map = list(data.get("mov_map") or [])
    for ref_index, mov_index in zip(ref_map, mov_map):
        if ref_index is None:
            slot = max(0, min(ref_len, last_ref + 1))
            if mov_index is not None:
                insertions[slot].append(int(mov_index))
            continue
        if 0 <= int(ref_index) < ref_len:
            ref_index = int(ref_index)
            if mov_index is not None:
                by_ref[ref_index] = int(mov_index)
            last_ref = ref_index
    return {"insertions": insertions, "by_ref": by_ref}


def _entry_alignment_side_from_index_map(entry, row_id, index_map):
    sequence = str(entry.get("sequence") or "")
    aligned = []
    for index in index_map:
        if index is None or index < 0 or index >= len(sequence):
            aligned.append("-")
        else:
            aligned.append(sequence[index])
    return {
        "row_id": row_id,
        "spec": entry.get("spec", ""),
        "display": entry.get("display", entry.get("spec", row_id)),
        "sequence": sequence,
        "aligned": "".join(aligned),
        "_entry": entry,
        "_structure": _entry_structure(entry),
        "column_map": _entry_column_map(entry, index_map),
        "column_labels": _entry_column_labels(entry, index_map),
        "styles": _entry_style_map(entry, index_map),
        "metal_annotations": _entry_metal_annotation_map(entry, index_map),
    }


def _entry_pair_alignment_indices(reference, moving):
    alignment = _structure_position_align(reference, moving)
    if alignment is None:
        ref_aligned, mov_aligned, ref_map, mov_map = _needleman_wunsch_align(
            reference.get("sequence", ""),
            moving.get("sequence", ""),
        )
        matched_pairs = 0
        source = "sequence"
        distance_cutoff = None
    else:
        ref_aligned, mov_aligned, ref_map, mov_map, matched_pairs, distance_cutoff = alignment
        source = "scene_position"
    return {
        "ref_aligned": ref_aligned,
        "mov_aligned": mov_aligned,
        "ref_map": ref_map,
        "mov_map": mov_map,
        "matched_pairs": matched_pairs,
        "source": source,
        "distance_cutoff": distance_cutoff,
    }


def _entry_pair_alignment_payload(reference, moving):
    data = _entry_pair_alignment_indices(reference, moving)
    ref_aligned = data["ref_aligned"]
    mov_aligned = data["mov_aligned"]
    ref_map = data["ref_map"]
    mov_map = data["mov_map"]
    matched_pairs = data["matched_pairs"]
    source = data["source"]
    distance_cutoff = data["distance_cutoff"]
    ref_spec = reference.get("spec", "")
    mov_spec = moving.get("spec", "")
    alignment_id = (
        "codex_auto_pair_"
        + str(ref_spec or "ref").replace("#", "").replace("/", "_")
        + "_"
        + str(mov_spec or "mov").replace("#", "").replace("/", "_")
    )
    return {
        "alignment_id": alignment_id,
        "name": "Auto pairwise sequence alignment",
        "auto_pairwise": True,
        "alignment_source": source,
        "matched_pairs": matched_pairs,
        "distance_cutoff": distance_cutoff,
        "reference": {
            "spec": ref_spec,
            "display": reference.get("display", ref_spec or "reference"),
            "sequence": reference.get("sequence", ""),
            "aligned": ref_aligned,
            "_entry": reference,
            "_structure": _entry_structure(reference),
            "column_map": _entry_column_map(reference, ref_map),
            "column_labels": _entry_column_labels(reference, ref_map),
            "styles": _entry_style_map(reference, ref_map),
            "metal_annotations": _entry_metal_annotation_map(reference, ref_map),
        },
        "moving": {
            "spec": mov_spec,
            "display": moving.get("display", mov_spec or "moving"),
            "sequence": moving.get("sequence", ""),
            "aligned": mov_aligned,
            "_entry": moving,
            "_structure": _entry_structure(moving),
            "column_map": _entry_column_map(moving, mov_map),
            "column_labels": _entry_column_labels(moving, mov_map),
            "styles": _entry_style_map(moving, mov_map),
            "metal_annotations": _entry_metal_annotation_map(moving, mov_map),
        },
        "length": len(ref_aligned),
        "identity": _aligned_identity(ref_aligned, mov_aligned),
    }


def _structure_position_align(reference, moving, *, max_distance=4.0, gap=-2.0):
    ref_seq = str(reference.get("sequence") or "")
    mov_seq = str(moving.get("sequence") or "")
    ref_coords = _entry_scene_coords(reference)
    mov_coords = _entry_scene_coords(moving)
    if not ref_seq or not mov_seq or not ref_coords or not mov_coords:
        return None
    if len(ref_coords) != len(ref_seq) or len(mov_coords) != len(mov_seq):
        return None

    n = len(ref_seq)
    m = len(mov_seq)
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    trace = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0] = i * gap
        trace[i][0] = "up"
    for j in range(1, m + 1):
        score[0][j] = j * gap
        trace[0][j] = "left"

    far_penalty = gap * 2.5
    for i in range(1, n + 1):
        ref_coord = ref_coords[i - 1]
        for j in range(1, m + 1):
            mov_coord = mov_coords[j - 1]
            pair_score = far_penalty
            if ref_coord is not None and mov_coord is not None:
                dist = _coord_distance(ref_coord, mov_coord)
                if dist <= max_distance:
                    identity_bonus = 0.35 if ref_seq[i - 1] == mov_seq[j - 1] else 0.0
                    pair_score = 6.0 - dist + identity_bonus
            diag = score[i - 1][j - 1] + pair_score
            up = score[i - 1][j] + gap
            left = score[i][j - 1] + gap
            best = diag
            direction = "diag"
            if up > best:
                best = up
                direction = "up"
            if left > best:
                best = left
                direction = "left"
            score[i][j] = best
            trace[i][j] = direction

    aligned_ref = []
    aligned_mov = []
    ref_map = []
    mov_map = []
    matched_pairs = 0
    i, j = n, m
    while i > 0 or j > 0:
        direction = trace[i][j] if i >= 0 and j >= 0 else None
        if direction == "diag" and i > 0 and j > 0:
            ref_coord = ref_coords[i - 1]
            mov_coord = mov_coords[j - 1]
            if ref_coord is not None and mov_coord is not None and _coord_distance(ref_coord, mov_coord) <= max_distance:
                aligned_ref.append(ref_seq[i - 1])
                aligned_mov.append(mov_seq[j - 1])
                ref_map.append(i - 1)
                mov_map.append(j - 1)
                matched_pairs += 1
                i -= 1
                j -= 1
                continue
            # A distant diagonal is just a traceback tie-break artifact; make it
            # explicit as insertion/deletion columns so the visual alignment
            # reflects missing structural correspondence.
            aligned_ref.append("-")
            aligned_mov.append(mov_seq[j - 1])
            ref_map.append(None)
            mov_map.append(j - 1)
            j -= 1
            aligned_ref.append(ref_seq[i - 1])
            aligned_mov.append("-")
            ref_map.append(i - 1)
            mov_map.append(None)
            i -= 1
        elif (direction == "up" and i > 0) or (i > 0 and j == 0):
            aligned_ref.append(ref_seq[i - 1])
            aligned_mov.append("-")
            ref_map.append(i - 1)
            mov_map.append(None)
            i -= 1
        else:
            aligned_ref.append("-")
            aligned_mov.append(mov_seq[j - 1])
            ref_map.append(None)
            mov_map.append(j - 1)
            j -= 1

    aligned_ref.reverse()
    aligned_mov.reverse()
    ref_map.reverse()
    mov_map.reverse()
    if matched_pairs < 3:
        return None
    return "".join(aligned_ref), "".join(aligned_mov), ref_map, mov_map, matched_pairs, max_distance


def _entry_scene_coords(entry):
    residues = entry.get("_residue_objects") or []
    coords = []
    for residue in residues:
        coord = None
        try:
            atom = getattr(residue, "principal_atom", None)
            if atom is not None:
                coord = _coord_tuple(atom.scene_coord)
        except Exception:
            coord = None
        coords.append(coord)
    return coords


def _coord_tuple(coord):
    try:
        return (float(coord[0]), float(coord[1]), float(coord[2]))
    except Exception:
        values = list(coord)
        return (float(values[0]), float(values[1]), float(values[2]))


def _coord_distance(a, b):
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    dz = a[2] - b[2]
    return ((dx * dx) + (dy * dy) + (dz * dz)) ** 0.5


def _entry_column_map(entry, index_map):
    residues = entry.get("residues") or []
    column_map = []
    for index in index_map:
        if index is None or index < 0 or index >= len(residues):
            column_map.append(None)
            continue
        column_map.append(residues[index].get("spec"))
    return column_map


def _entry_column_labels(entry, index_map):
    residues = entry.get("residues") or []
    labels = []
    for index in index_map:
        if index is None or index < 0 or index >= len(residues):
            labels.append(None)
            continue
        labels.append(_residue_payload_alignment_label(residues[index]))
    return labels


def _entry_style_map(entry, index_map):
    styles = entry.get("residue_styles") or []
    mapped = []
    for index in index_map:
        if index is None or index < 0 or index >= len(styles):
            mapped.append(None)
            continue
        mapped.append(styles[index])
    return mapped


def _entry_metal_annotation_map(entry, index_map):
    annotations = entry.get("metal_annotations") or []
    mapped = []
    for index in index_map:
        if index is None or index < 0 or index >= len(annotations):
            mapped.append(None)
            continue
        mapped.append(annotations[index] or None)
    return mapped


def _needleman_wunsch_align(seq_a, seq_b, match=2, mismatch=-1, gap=-2):
    a = str(seq_a or "")
    b = str(seq_b or "")
    n = len(a)
    m = len(b)
    if n == 0 and m == 0:
        return "", "", [], []

    score = [[0] * (m + 1) for _ in range(n + 1)]
    trace = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0] = i * gap
        trace[i][0] = "up"
    for j in range(1, m + 1):
        score[0][j] = j * gap
        trace[0][j] = "left"

    for i in range(1, n + 1):
        ca = a[i - 1]
        for j in range(1, m + 1):
            cb = b[j - 1]
            diag = score[i - 1][j - 1] + (match if ca == cb else mismatch)
            up = score[i - 1][j] + gap
            left = score[i][j - 1] + gap
            best = diag
            direction = "diag"
            if up > best:
                best = up
                direction = "up"
            if left > best:
                best = left
                direction = "left"
            score[i][j] = best
            trace[i][j] = direction

    aligned_a = []
    aligned_b = []
    map_a = []
    map_b = []
    i, j = n, m
    while i > 0 or j > 0:
        direction = trace[i][j] if i >= 0 and j >= 0 else None
        if direction == "diag" or (i > 0 and j > 0 and direction is None):
            aligned_a.append(a[i - 1])
            aligned_b.append(b[j - 1])
            map_a.append(i - 1)
            map_b.append(j - 1)
            i -= 1
            j -= 1
        elif direction == "up" or (i > 0 and j == 0):
            aligned_a.append(a[i - 1])
            aligned_b.append("-")
            map_a.append(i - 1)
            map_b.append(None)
            i -= 1
        else:
            aligned_a.append("-")
            aligned_b.append(b[j - 1])
            map_a.append(None)
            map_b.append(j - 1)
            j -= 1

    aligned_a.reverse()
    aligned_b.reverse()
    map_a.reverse()
    map_b.reverse()
    return "".join(aligned_a), "".join(aligned_b), map_a, map_b


def _format_alignment_panel(payload):
    rows = _alignment_payload_rows(payload)
    if len(rows) > 2:
        return _format_multi_alignment_panel(payload, rows)

    ref = payload.get("reference", {})
    mov = payload.get("moving", {})
    ref_aligned = str(ref.get("aligned", ""))
    mov_aligned = str(mov.get("aligned", ""))
    length = min(len(ref_aligned), len(mov_aligned))
    ref_aligned = ref_aligned[:length]
    mov_aligned = mov_aligned[:length]
    payload["length"] = length
    match_line = []
    for a, b in zip(ref_aligned, mov_aligned):
        if _is_alignment_gap(a) or _is_alignment_gap(b):
            match_line.append(" ")
        elif a == b:
            match_line.append("|")
        else:
            match_line.append(".")
    ref_label = _alignment_row_label(ref, "reference")
    mov_label = _alignment_row_label(mov, "moving")
    label_width = min(42, max(6, len(ref_label), len(mov_label), len("align")))
    ref_label = _fit_alignment_label(ref_label, label_width)
    mov_label = _fit_alignment_label(mov_label, label_width)
    offset = label_width + 2
    blank_label = " " * label_width
    ruler = _alignment_ruler(length)
    lines = [f"{'align':<{label_width}}  {ruler}"]
    row_lines = {"ruler": 0}
    metal_ranges = []
    ref_suffix, ref_metal_ranges = _alignment_side_metal_suffix(ref, length)
    lines.append(f"{ref_label:<{label_width}}  {ref_aligned}{ref_suffix}")
    row_lines["reference"] = len(lines) - 1
    metal_ranges.extend(("reference", start, end, metal) for start, end, metal in ref_metal_ranges)
    lines.append(f"{blank_label}  {''.join(match_line)}")
    row_lines["match"] = len(lines) - 1
    mov_suffix, mov_metal_ranges = _alignment_side_metal_suffix(mov, length)
    lines.append(f"{mov_label:<{label_width}}  {mov_aligned}{mov_suffix}")
    row_lines["moving"] = len(lines) - 1
    metal_ranges.extend(("moving", start, end, metal) for start, end, metal in mov_metal_ranges)
    payload["display_layout"] = {
        "offset": offset,
        "sequence_rows": ["reference", "moving"],
        "row_lines": row_lines,
        "metal_ranges": metal_ranges,
    }
    display_rows = [("reference", ref, ref_label), ("moving", mov, mov_label)]
    payload["display_tooltips"] = _alignment_column_tooltips(payload, display_rows, length)
    payload["display_statuses"] = _alignment_column_statuses(payload, display_rows, length)
    return "\n".join(lines), min(176, max(112, 38 + (len(lines) * 22)))


def _format_multi_alignment_panel(payload, rows):
    aligned_rows = []
    lengths = [len(str(row.get("aligned") or "")) for row in rows]
    length = min(lengths) if lengths else 0
    if length <= 0:
        payload["length"] = 0
        return "", 0
    for row in rows:
        row["aligned"] = str(row.get("aligned") or "")[:length]
        for key in ("column_map", "column_labels", "styles", "metal_annotations"):
            value = row.get(key)
            if value is not None:
                row[key] = list(value)[:length]
        aligned_rows.append(row)
    payload["length"] = length
    labels = [_alignment_row_label(row, f"row {index + 1}") for index, row in enumerate(aligned_rows)]
    label_width = min(42, max([len("align"), len("cons"), len("metal")] + [len(label) for label in labels]))
    labels = [_fit_alignment_label(label, label_width) for label in labels]
    offset = label_width + 2
    ruler = _alignment_ruler(length)
    lines = [f"{'align':<{label_width}}  {ruler}"]
    row_lines = {"ruler": 0}
    sequence_rows = []
    display_rows = []
    metal_ranges = []
    for index, (row, label) in enumerate(zip(aligned_rows, labels), start=1):
        row_id = str(row.get("row_id") or ("reference" if index == 1 else f"row{index - 1}"))
        row["row_id"] = row_id
        row_lines[row_id] = len(lines)
        sequence_rows.append(row_id)
        display_rows.append((row_id, row, label))
        suffix, ranges = _alignment_side_metal_suffix(row, length)
        lines.append(f"{label:<{label_width}}  {row.get('aligned', '')}{suffix}")
        metal_ranges.extend((row_id, start, end, metal) for start, end, metal in ranges)
    consensus_line = _multi_alignment_consensus_line(aligned_rows, length)
    row_lines["consensus"] = len(lines)
    lines.append(f"{'cons':<{label_width}}  {consensus_line}")
    payload["rows"] = aligned_rows
    payload["reference"] = aligned_rows[0]
    payload["moving"] = aligned_rows[1] if len(aligned_rows) > 1 else {}
    payload["identity"] = _average_identity_vs_reference(aligned_rows)
    payload["display_layout"] = {
        "offset": offset,
        "sequence_rows": sequence_rows,
        "row_lines": row_lines,
        "metal_ranges": metal_ranges,
    }
    payload["display_tooltips"] = _alignment_column_tooltips(payload, display_rows, length)
    payload["display_statuses"] = _alignment_column_statuses(payload, display_rows, length)
    height = min(220, max(112, 38 + (len(lines) * 22)))
    return "\n".join(lines), height


def _multi_alignment_consensus_line(rows, length):
    chars = []
    for column in range(max(0, int(length or 0))):
        residues = []
        for row in rows:
            aligned = str(row.get("aligned") or "")
            if column >= len(aligned):
                continue
            ch = aligned[column]
            if not _is_alignment_gap(ch):
                residues.append(ch.upper())
        if not residues:
            chars.append(" ")
        elif len(residues) == len(rows) and len(set(residues)) == 1:
            chars.append("|")
        elif len(set(residues)) == 1:
            chars.append(":")
        elif len(residues) >= 2:
            chars.append(".")
        else:
            chars.append(" ")
    return "".join(chars)


def _alignment_row_label(side, fallback):
    display = str(side.get("display") or side.get("spec") or fallback).strip()
    if " · " in display:
        left, right = display.split(" · ", 1)
        display = f"{left} {right}"
    display = display.replace(".pdb", "")
    return " ".join(display.split()) or fallback


def _fit_alignment_label(label, width):
    label = str(label or "").strip()
    if len(label) <= width:
        return label
    if width <= 3:
        return label[:width]
    return label[: width - 3] + "..."


def _alignment_ruler(length, step=10):
    chars = [" "] * max(0, int(length or 0))
    if not chars:
        return ""
    for value in range(step, len(chars) + 1, step):
        text = str(value)
        start = max(0, value - 1)
        for offset, ch in enumerate(text):
            index = start + offset
            if 0 <= index < len(chars):
                chars[index] = ch
    return "".join(chars)


def _alignment_column_tooltips(payload, display_rows, length):
    tooltips = []
    for column in range(max(0, int(length or 0))):
        lines = [f"alignment column {column + 1}"]
        for _row_id, side, label in display_rows:
            lines.append(_alignment_side_tooltip(label, side, str(side.get("aligned", "")), column))
        tooltips.append("\n".join(line for line in lines if line))
    return tooltips


def _alignment_column_statuses(payload, display_rows, length):
    statuses = []
    for column in range(max(0, int(length or 0))):
        bits = []
        for _row_id, side, label in display_rows:
            bits.append(f"{label}: {_alignment_side_residue_text(side, str(side.get('aligned', '')), column)}")
        statuses.append(f"alignment column {column + 1} · " + " | ".join(bits))
    return statuses


def _alignment_selected_columns_from_session(session, payload):
    selected_keys = _selected_residue_keys_from_session(session)
    row_items = _alignment_payload_row_items(payload)
    if not selected_keys:
        return {row_name: [] for row_name, _side in row_items}
    result = {}
    for row_name, side in row_items:
        columns = []
        for column, spec in enumerate(side.get("column_map") or []):
            if _residue_spec_matches_keys(spec, selected_keys):
                columns.append(column)
        result[row_name] = _indices_to_ranges(columns)
    return result


def _first_alignment_selected_column(selected_columns):
    starts = []
    for ranges in (selected_columns or {}).values():
        starts.extend(start for start, _end in ranges or [])
    return min(starts) if starts else None


def _alignment_selected_status(payload, selected_columns):
    first_column = _first_alignment_selected_column(selected_columns)
    if first_column is None:
        return ""
    statuses = list(payload.get("display_statuses") or [])
    if not (0 <= first_column < len(statuses)):
        return ""
    total = sum((end - start + 1) for ranges in (selected_columns or {}).values() for start, end in (ranges or []))
    prefix = "Selected"
    if total > 1:
        prefix = f"Selected {total} alignment residue(s)"
    return f"{prefix} · {statuses[first_column]}"


def _residue_spec_matches_keys(spec, selected_keys):
    spec = str(spec or "").strip()
    if not spec:
        return False
    return spec in selected_keys or _canonical_residue_key_from_spec(spec) in selected_keys


def _canonical_residue_key_from_spec(spec):
    spec = str(spec or "").strip()
    if not spec or ":" not in spec:
        return ""
    model_chain, number = spec.rsplit(":", 1)
    if "/" not in model_chain:
        return ""
    model_spec, chain_id = model_chain.rsplit("/", 1)
    return f"{model_spec}|{chain_id}|{number}"


def _indices_to_ranges(indices):
    values = sorted(set(int(index) for index in indices or []))
    if not values:
        return []
    ranges = []
    start = previous = values[0]
    for index in values[1:]:
        if index == previous + 1:
            previous = index
            continue
        ranges.append((start, previous))
        start = previous = index
    ranges.append((start, previous))
    return ranges


def _alignment_side_tooltip(label, side, aligned, column):
    return f"{label}: {_alignment_side_residue_text(side, aligned, column)}"


def _alignment_side_residue_text(side, aligned, column):
    if column >= len(aligned):
        return "gap"
    aa = aligned[column]
    metal_text = _metal_annotation_text(_alignment_side_metal_annotations(side, column))
    if _is_alignment_gap(aa):
        return f"gap; {metal_text}" if metal_text else "gap"
    column_labels = list(side.get("column_labels") or [])
    if column < len(column_labels) and column_labels[column]:
        residue_text = str(column_labels[column])
        return f"{residue_text}; {metal_text}" if metal_text else residue_text
    column_map = list(side.get("column_map") or [])
    spec = column_map[column] if column < len(column_map) else None
    if spec:
        parsed = _alignment_label_from_spec(spec, aa)
        if parsed:
            return f"{parsed}; {metal_text}" if metal_text else parsed
    residue_text = str(aa).upper()
    return f"{residue_text}; {metal_text}" if metal_text else residue_text


def _residue_payload_alignment_label(residue):
    if not isinstance(residue, dict):
        return None
    number = str(residue.get("number") or "").strip()
    name = str(residue.get("name") or "").strip().upper()
    letter = str(residue.get("letter") or AA3_TO_1.get(name, "X")).strip().upper()
    chain = _chain_id_from_spec(residue.get("spec")) or ""
    prefix = _residue_position_prefix(chain, number)
    if name:
        return f"{prefix} {name}({letter})".strip()
    return f"{prefix} {letter}".strip() if prefix else letter


def _residue_object_alignment_label(residue, chain_id=None):
    if residue is None:
        return None
    chain = str(chain_id or getattr(residue, "chain_id", "") or "").strip()
    number = str(getattr(residue, "number", "") or "").strip()
    name = str(getattr(residue, "name", "") or "").strip().upper()
    letter = AA3_TO_1.get(name, "X")
    prefix = _residue_position_prefix(chain, number)
    if name:
        return f"{prefix} {name}({letter})".strip()
    return f"{prefix} {letter}".strip() if prefix else letter


def _alignment_label_from_spec(spec, aa):
    spec = str(spec or "").strip()
    if not spec:
        return ""
    chain = _chain_id_from_spec(spec)
    number = ""
    if ":" in spec:
        number = spec.rsplit(":", 1)[-1].strip()
    prefix = _residue_position_prefix(chain, number)
    letter = str(aa or "").upper()
    return f"{prefix} {letter}".strip() if prefix else letter


def _residue_position_prefix(chain, number):
    chain = str(chain or "").strip()
    number = str(number or "").strip()
    if chain and number:
        return f"Chain {chain} {number}"
    if chain:
        return f"Chain {chain}"
    return number


def _chain_id_from_spec(spec):
    spec = str(spec or "").strip()
    if "/" not in spec:
        return ""
    after_slash = spec.rsplit("/", 1)[-1]
    if ":" in after_slash:
        return after_slash.split(":", 1)[0].strip()
    return after_slash.strip()


def _alignment_ungapped_positions(aligned):
    positions = []
    count = 0
    for ch in str(aligned or ""):
        if _is_alignment_gap(ch):
            positions.append(None)
        else:
            count += 1
            positions.append(count)
    return positions


def _is_alignment_gap(char):
    return char in ("-", ".")


def _aligned_identity(aligned_a, aligned_b):
    matches = 0
    aligned = 0
    for a, b in zip(aligned_a, aligned_b):
        if _is_alignment_gap(a) or _is_alignment_gap(b):
            continue
        aligned += 1
        if a == b:
            matches += 1
    return (matches / aligned) if aligned else 0.0


def _average_identity_vs_reference(rows):
    rows = list(rows or [])
    if len(rows) < 2:
        return 0.0
    reference = str(rows[0].get("aligned") or "")
    values = [
        _aligned_identity(reference, str(row.get("aligned") or ""))
        for row in rows[1:]
    ]
    return (sum(values) / len(values)) if values else 0.0


def _rgba8(value):
    if value is None:
        return None
    try:
        values = list(value)
    except Exception:
        return None
    if len(values) < 3:
        return None
    if len(values) == 3:
        values.append(255)
    try:
        channels = [float(v) for v in values[:4]]
    except Exception:
        return None
    if channels and max(channels) <= 1.0:
        channels = [v * 255.0 for v in channels]
    return tuple(max(0, min(255, int(round(v)))) for v in channels[:4])


def _residue_display_rgba(residue):
    if residue is None:
        return None, ""
    try:
        if bool(getattr(residue, "ribbon_display", False)):
            rgba = _rgba8(getattr(residue, "ribbon_color", None))
            if rgba is not None:
                return rgba, "cartoon"
    except Exception:
        pass
    try:
        atoms = getattr(residue, "atoms", None)
        if atoms is not None and len(atoms):
            from chimerax.atomic.colors import average_color

            rgba = _rgba8(average_color(atoms))
            if rgba is not None:
                return rgba, "atoms"
    except Exception:
        pass
    return None, ""


def _residue_has_display(residue):
    if residue is None:
        return False
    try:
        if bool(getattr(residue, "ribbon_display", False)):
            return True
    except Exception:
        pass
    try:
        atoms = getattr(residue, "atoms", None)
        if atoms is not None and len(atoms):
            visibles = getattr(atoms, "visibles", None)
            if visibles is not None and bool(visibles.any()):
                return True
            displays = getattr(atoms, "displays", None)
            if displays is not None and bool(displays.any()):
                return True
    except Exception:
        pass
    return False


def _residue_has_visible_atoms(residue):
    if residue is None:
        return False
    try:
        atoms = getattr(residue, "atoms", None)
        if atoms is None or not len(atoms):
            return False
        visibles = getattr(atoms, "visibles", None)
        if visibles is not None:
            return bool(visibles.any())
        displays = getattr(atoms, "displays", None)
        if displays is not None:
            return bool(displays.any())
        return False
    except Exception:
        return False


def _residue_has_stick_atoms(residue):
    if residue is None:
        return False
    try:
        atoms = getattr(residue, "atoms", None)
        if atoms is None or not len(atoms):
            return False
        from chimerax.atomic import Atom

        draw_modes = getattr(atoms, "draw_modes", None)
        if draw_modes is not None:
            stick_mask = draw_modes == Atom.STICK_STYLE
            visibles = getattr(atoms, "visibles", None)
            if visibles is not None:
                stick_mask = stick_mask & visibles
            else:
                displays = getattr(atoms, "displays", None)
                if displays is not None:
                    stick_mask = stick_mask & displays
            return bool(stick_mask.any())
    except Exception:
        pass
    try:
        for atom in getattr(residue, "atoms", []) or []:
            stick_style = getattr(atom, "STICK_STYLE", 2)
            if getattr(atom, "draw_mode", None) != stick_style:
                continue
            if bool(getattr(atom, "visible", False)) or bool(getattr(atom, "display", False)):
                return True
    except Exception:
        pass
    return False


def _residue_display_style(residue):
    rgba, source = _residue_display_rgba(residue)
    if rgba is None:
        return None
    r, g, b, a = rgba
    if not _residue_has_display(residue):
        a = min(a, 70)
    atoms_displayed = _residue_has_visible_atoms(residue)
    stick_displayed = _residue_has_stick_atoms(residue)
    return {
        "rgba": (r, g, b, a),
        "source": source,
        "atoms_displayed": atoms_displayed,
        "stick_displayed": stick_displayed,
    }


def _sequence_text_style(style):
    if not style:
        return None
    rgba = _rgba8(style.get("rgba") if isinstance(style, dict) else None)
    if rgba is None:
        return None
    r, g, b, a = rgba
    stick_displayed = bool(style.get("stick_displayed"))
    atoms_displayed = bool(style.get("atoms_displayed"))
    fg_r, fg_g, fg_b = _contrast_sequence_rgb(r, g, b) if stick_displayed else _legible_sequence_rgb(r, g, b)
    opacity = max(0.0, min(1.0, a / 255.0))
    if stick_displayed:
        fg_alpha = 255
        bg_alpha = max(190, min(235, int(round(190 + (45 * opacity)))))
    else:
        fg_alpha = max(88, min(255, int(round(76 + (179 * opacity)))))
        bg_alpha = max(14, min(76, int(round(14 + (62 * opacity)))))
    return {
        "foreground": QColor(fg_r, fg_g, fg_b, fg_alpha),
        "background": QColor(r, g, b, bg_alpha),
        "font_weight": 900 if stick_displayed else (700 if atoms_displayed else None),
        "font_underline": True if (stick_displayed or atoms_displayed) else None,
    }


def _contrast_sequence_rgb(r, g, b):
    luminance = (0.2126 * r) + (0.7152 * g) + (0.0722 * b)
    return (0, 0, 0) if luminance >= 145 else (255, 255, 255)


def _legible_sequence_rgb(r, g, b):
    luminance = (0.2126 * r) + (0.7152 * g) + (0.0722 * b)
    if luminance >= 108:
        return int(r), int(g), int(b)
    mix = min(0.58, max(0.18, (118 - luminance) / 180.0))
    return (
        int(round(r + (255 - r) * mix)),
        int(round(g + (255 - g) * mix)),
        int(round(b + (255 - b) * mix)),
    )


def _style_tooltip(style):
    if not style:
        return ""
    rgba = _rgba8(style.get("rgba") if isinstance(style, dict) else None)
    if rgba is None:
        return ""
    r, g, b, a = rgba
    source = style.get("source") or "display"
    bits = [f"{source} #{r:02X}{g:02X}{b:02X}", f"opacity {round(a / 255 * 100)}%"]
    if style.get("stick_displayed"):
        bits.append("stick shown")
    if style.get("atoms_displayed"):
        bits.append("atoms shown")
    return ", ".join(bits)


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
    metal_annotations = _metal_annotations_for_chain(chain, residue_objects)

    sequence = "".join(AA3_TO_1.get(name, "X") for name in names)
    residues_payload = []
    residue_styles = []
    tooltips = []
    for index in range(length):
        residue = residue_objects[index] if index < len(residue_objects) else None
        spec = getattr(residue, "atomspec", "") if residue is not None else ""
        if not spec:
            spec = f"{model_spec}/{chain_id}:{numbers[index]}"
        label = f"{_residue_position_prefix(chain_id, numbers[index])} {names[index]}({sequence[index]})"
        ss_id = getattr(residue, "ss_id", ss_ids[index] if index < len(ss_ids) else 0)
        ss_type = getattr(residue, "ss_type", ss_types[index] if index < len(ss_types) else 0)
        is_helix = getattr(residue, "is_helix", helix_flags[index] if index < len(helix_flags) else False)
        is_strand = getattr(residue, "is_strand", strand_flags[index] if index < len(strand_flags) else False)
        is_helix = bool(is_helix) or _safe_int(ss_type) == 1
        is_strand = bool(is_strand) or _safe_int(ss_type) == 2
        display_style = _residue_display_style(residue)
        residue_styles.append(display_style)
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
                "display_style": display_style,
            }
        )
        style_tip = _style_tooltip(display_style)
        metal_tip = _metal_annotation_text(
            metal_annotations[index] if index < len(metal_annotations) else None,
            detailed=True,
        )
        tooltip_bits = [label]
        if style_tip:
            tooltip_bits.append(style_tip)
        if metal_tip:
            tooltip_bits.append(metal_tip)
        tooltip_bits.append("click or drag to select")
        tooltips.append(" · ".join(tooltip_bits))

    return {
        "model_spec": model_spec,
        "model_name": model_name,
        "chain_id": chain_id,
        "spec": f"{model_spec}/{chain_id}",
        "display": f"{model_spec}/{chain_id} · {model_name}",
        "sequence": sequence,
        "length": length,
        "residues": residues_payload,
        "_residue_objects": residue_objects,
        "residue_styles": residue_styles,
        "metal_annotations": metal_annotations,
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
        start = max(0, pos - 1)
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


def _metal_line(entry):
    annotations = list(entry.get("metal_annotations") or [])
    if not annotations:
        return ""
    chars = [" "] * int(entry.get("length", 0) or 0)
    for index, items in enumerate(annotations[: len(chars)]):
        marker = _metal_marker_char(items)
        if marker:
            chars[index] = marker
    return "".join(chars).rstrip()


def _metal_panel_entries_for_entry(entry):
    structure = _entry_structure(entry)
    if structure is None:
        return []
    return _metal_panel_entries_for_structure(structure, entry=entry)


def _metal_panel_entries_for_alignment_side(side):
    if not side:
        return []
    entry = side.get("_entry")
    if entry:
        return _metal_panel_entries_for_entry(entry)
    structure = side.get("_structure")
    if structure is None:
        return []
    return _metal_panel_entries_for_structure(structure, entry=None)


def _metal_panel_entries_for_structure(structure, entry=None):
    metals = _metal_atoms_for_structure(structure)
    if not metals:
        return []
    counts = {}
    result = []
    for metal in metals:
        element = str(metal.get("element") or "M").upper()
        counts[element] = counts.get(element, 0) + 1
        token = f"{_metal_token_label(element)}({counts[element]})"
        spec = str(metal.get("spec") or "").strip()
        visible = _atom_is_visible(metal.get("atom"))
        contact_summary = _metal_contact_summary_for_spec(entry, spec) if entry else ""
        state = "shown" if visible else "hidden"
        tooltip_bits = [f"{token} {metal.get('label') or spec}", state]
        if contact_summary:
            tooltip_bits.append(contact_summary)
        item = dict(metal)
        item.update(
            {
                "token": token,
                "visible": visible,
                "tooltip": " · ".join(bit for bit in tooltip_bits if bit),
            }
        )
        result.append(item)
    return result


def _alignment_side_metal_suffix(side, alignment_length):
    return _sequence_metal_suffix(
        _metal_panel_entries_for_alignment_side(side),
        int(alignment_length or 0),
    )


def _sequence_metal_suffix(metals, sequence_length):
    metals = list(metals or [])
    if not metals:
        return "", []
    text = "  "
    position = int(sequence_length or 0) + len(text)
    ranges = []
    for index, metal in enumerate(metals):
        token = str(metal.get("token") or metal.get("element") or "M").strip()
        if index:
            text += "  "
            position += 2
        start = position
        text += token
        position += len(token)
        ranges.append((start, position, metal))
    return text, ranges


def _entry_structure(entry):
    if not entry:
        return None
    for residue in entry.get("_residue_objects") or []:
        structure = getattr(residue, "structure", None)
        if structure is not None:
            return structure
    return None


def _metal_token_element(element):
    element = str(element or "M").strip().upper()
    if len(element) <= 1:
        return element or "M"
    return element[0] + element[1:].lower()


def _metal_token_label(element):
    element = str(element or "M").strip().upper()
    charge_labels = {
        "CA": "Ca2+",
        "MG": "Mg2+",
        "ZN": "Zn2+",
        "MN": "Mn2+",
        "CU": "Cu2+",
        "CO": "Co2+",
        "NI": "Ni2+",
        "FE": "Fe",
        "NA": "Na+",
        "K": "K+",
        "LI": "Li+",
        "CS": "Cs+",
        "RB": "Rb+",
        "SR": "Sr2+",
        "BA": "Ba2+",
        "CD": "Cd2+",
    }
    return charge_labels.get(element, _metal_token_element(element))


def _atom_is_visible(atom):
    if atom is None:
        return False
    try:
        return bool(getattr(atom, "visible", False))
    except Exception:
        pass
    try:
        return bool(getattr(atom, "display", False))
    except Exception:
        return False


def _metal_contact_summary_for_spec(entry, spec):
    spec = str(spec or "").strip()
    if not entry or not spec:
        return ""
    contacts = []
    for index, items in enumerate(entry.get("metal_annotations") or []):
        residue = (entry.get("residues") or [])[index] if index < len(entry.get("residues") or []) else {}
        for item in items or []:
            if str(item.get("spec") or "") != spec:
                continue
            residue_label = residue.get("label") or residue.get("number") or ""
            donor = str(item.get("donor_atom") or "").strip()
            try:
                dist = f"{float(item.get('distance')):.2f} A"
            except Exception:
                dist = ""
            relation = "coord" if item.get("direct") else "near"
            bits = [relation, str(residue_label)]
            if donor:
                bits.append(donor)
            if dist:
                bits.append(dist)
            contacts.append(" ".join(bits))
    if not contacts:
        return ""
    text = "; ".join(contacts[:4])
    if len(contacts) > 4:
        text += f"; +{len(contacts) - 4}"
    return text


def _alignment_metal_line(side, length):
    chars = [" "] * max(0, int(length or 0))
    annotations = list(side.get("metal_annotations") or [])
    for index, items in enumerate(annotations[: len(chars)]):
        marker = _metal_marker_char(items)
        if marker:
            chars[index] = marker
    return "".join(chars).rstrip()


def _alignment_side_metal_annotations(side, column):
    annotations = list(side.get("metal_annotations") or [])
    if 0 <= int(column) < len(annotations):
        return annotations[int(column)]
    return None


def _metal_marker_char(items):
    records = [item for item in (items or []) if isinstance(item, dict)]
    if not records:
        return ""
    elements = []
    for item in records:
        element = str(item.get("element") or "M").upper()
        if element and element not in elements:
            elements.append(element)
    if len(elements) > 1:
        return "*"
    element = elements[0] if elements else "M"
    return element[:1] if element else "M"


def _metal_summary_text(entry):
    annotations = list(entry.get("metal_annotations") or [])
    seen = {}
    direct_residues = 0
    for items in annotations:
        if not items:
            continue
        direct_here = False
        for item in items:
            if not isinstance(item, dict):
                continue
            spec = str(item.get("spec") or "")
            element = str(item.get("element") or "M").upper()
            key = spec or item.get("label") or element
            seen[key] = element
            if item.get("direct"):
                direct_here = True
        if direct_here:
            direct_residues += 1
    if not seen:
        return ""
    counts = {}
    for element in seen.values():
        counts[element] = counts.get(element, 0) + 1
    count_text = ", ".join(f"{element}x{count}" if count > 1 else element for element, count in sorted(counts.items()))
    return f"metals {count_text}; {direct_residues} contact residue(s)"


def _metal_annotation_text(items, detailed=False):
    records = [item for item in (items or []) if isinstance(item, dict)]
    if not records:
        return ""
    parts = []
    for item in records[:4]:
        element = str(item.get("element") or "M").upper()
        label = str(item.get("label") or item.get("spec") or element)
        distance = item.get("distance")
        donor = str(item.get("donor_atom") or "").strip()
        relation = "coordinates" if item.get("direct") else "near"
        try:
            dist_text = f"{float(distance):.2f} A"
        except Exception:
            dist_text = ""
        if detailed:
            bit = f"{relation} {label}"
            if donor:
                bit += f" via {donor}"
            if dist_text:
                bit += f" ({dist_text})"
        else:
            bit = f"{element}"
            if dist_text:
                bit += f" {dist_text}"
        parts.append(bit)
    if len(records) > 4:
        parts.append(f"+{len(records) - 4} metal contact(s)")
    return "metal: " + "; ".join(parts)


def _metal_annotations_for_chain(chain, residue_objects=None):
    source = residue_objects if residue_objects is not None else getattr(chain, "existing_residues", [])
    try:
        residues = list(source)
    except Exception:
        residues = []
    if not residues:
        return []
    structure = getattr(chain, "structure", None)
    if structure is None:
        structure = getattr(residues[0], "structure", None)
    if structure is None:
        return [[] for _ in residues]
    annotations = [[] for _ in residues]
    metals = _metal_atoms_for_structure(structure)
    if not metals:
        return annotations

    for metal in metals:
        direct_hits = []
        fallback_hit = None
        cutoff = METAL_COORDINATION_CUTOFFS.get(metal["element"], 3.25)
        for index, residue in enumerate(residues):
            hit = _metal_residue_contact(metal, residue, cutoff=cutoff)
            if hit is not None:
                direct_hits.append((index, hit))
                continue
            nearest = _metal_residue_nearest_anchor(metal, residue)
            if nearest is None:
                continue
            if fallback_hit is None or nearest["distance"] < fallback_hit[1]["distance"]:
                fallback_hit = (index, nearest)
        if direct_hits:
            for index, hit in direct_hits:
                annotations[index].append(hit)
        elif fallback_hit is not None and fallback_hit[1]["distance"] <= 8.0:
            index, hit = fallback_hit
            annotations[index].append(hit)
    return annotations


def _metal_atoms_for_structure(structure):
    atoms = getattr(structure, "atoms", None)
    if atoms is None:
        return []
    metals = []
    for atom in atoms:
        if not _is_metal_atom(atom):
            continue
        element = _atom_element_symbol(atom)
        metals.append(
            {
                "atom": atom,
                "element": element,
                "coord": _atom_coord(atom),
                "spec": _atom_atomspec(atom),
                "label": _metal_atom_label(atom, element),
            }
        )
    return [metal for metal in metals if metal.get("coord") is not None]


def _metal_residue_contact(metal, residue, cutoff):
    metal_coord = metal.get("coord")
    if metal_coord is None:
        return None
    best = None
    for atom in getattr(residue, "atoms", []) or []:
        if not _is_coordination_donor_atom(atom):
            continue
        coord = _atom_coord(atom)
        if coord is None:
            continue
        distance = _coord_distance(metal_coord, coord)
        if distance > cutoff:
            continue
        if best is None or distance < best[0]:
            best = (distance, atom)
    if best is None:
        return None
    distance, donor_atom = best
    record = dict(metal)
    record.update(
        {
            "distance": distance,
            "donor_atom": str(getattr(donor_atom, "name", "") or ""),
            "direct": True,
        }
    )
    return record


def _metal_residue_nearest_anchor(metal, residue):
    metal_coord = metal.get("coord")
    if metal_coord is None:
        return None
    anchor = None
    try:
        anchor = getattr(residue, "principal_atom", None)
    except Exception:
        anchor = None
    if anchor is None:
        atoms = list(getattr(residue, "atoms", []) or [])
        anchor = atoms[0] if atoms else None
    coord = _atom_coord(anchor) if anchor is not None else None
    if coord is None:
        return None
    record = dict(metal)
    record.update(
        {
            "distance": _coord_distance(metal_coord, coord),
            "donor_atom": str(getattr(anchor, "name", "") or ""),
            "direct": False,
        }
    )
    return record


def _is_coordination_donor_atom(atom):
    try:
        if int(getattr(getattr(atom, "element", None), "number", 0) or 0) in METAL_DONOR_ELEMENT_NUMBERS:
            return True
    except Exception:
        pass
    name = str(getattr(atom, "name", "") or "").upper()
    return name.startswith(("OD", "OE", "OG", "OH", "ND", "NE", "NH", "NZ", "SD", "SG"))


def _is_metal_atom(atom):
    return _atom_element_symbol(atom) in METAL_ELEMENT_SYMBOLS


def _atom_element_symbol(atom):
    try:
        element = getattr(atom, "element", None)
        symbol = str(getattr(element, "name", "") or "").upper()
        if symbol:
            return symbol
    except Exception:
        pass
    name = str(getattr(atom, "name", "") or "").strip().upper()
    return "".join(ch for ch in name if ch.isalpha())[:2]


def _atom_atomspec(atom):
    spec = str(getattr(atom, "atomspec", "") or "").strip()
    if spec.startswith("#"):
        return spec
    residue = getattr(atom, "residue", None)
    structure = getattr(residue, "structure", None)
    model_id = getattr(structure, "id_string", None)
    if model_id and spec:
        return f"#{model_id}{spec}"
    return spec


def _metal_atom_label(atom, element):
    residue = getattr(atom, "residue", None)
    chain = str(getattr(residue, "chain_id", "") or "").strip()
    number = str(getattr(residue, "number", "") or "").strip()
    atom_name = str(getattr(atom, "name", "") or element).strip()
    if chain and number:
        return f"{element} Chain {chain} {number}@{atom_name}"
    if number:
        return f"{element} {number}@{atom_name}"
    spec = _atom_atomspec(atom)
    return f"{element} {spec}".strip()


def _atom_coord(atom):
    if atom is None:
        return None
    for attr in ("coord", "scene_coord"):
        try:
            coord = getattr(atom, attr)
            return _coord_tuple(coord)
        except Exception:
            pass
    return None
