"""Viewport-sized residue guides and outlines for fixed-width sequence strips."""

from Qt.QtCore import QEvent, QLineF, QPoint, QRectF, Qt
from Qt.QtGui import QColor, QPainter, QPalette, QPen, QTextCursor
from Qt.QtWidgets import QPlainTextEdit

from .sequence_colors import chemistry_color, normalize_base_palette


class ResidueColorText(QPlainTextEdit):
    """Keep chemistry fills and structure-color borders independent of picking."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._charge_colors = False
        self._base_colors = False
        self._base_palette = "muted"

    def set_residue_coloring(self, *, charge=False, bases=False, base_palette="muted"):
        charge, bases = bool(charge), bool(bases)
        base_palette = normalize_base_palette(base_palette)
        if (charge, bases, base_palette) == (self._charge_colors, self._base_colors, self._base_palette):
            return
        self._charge_colors, self._base_colors = charge, bases
        self._base_palette = base_palette
        self._refresh_color_formats()

    def _refresh_color_formats(self):
        # ExtraSelection caches also depend on the current color mode/palette.
        if hasattr(self, "_base_selection_cache_key"):
            self._base_selection_cache_key = None
            self._apply_highlights()
        if hasattr(self, "_style_selection_cache_key"):
            self._style_selection_cache_key = None
            self._apply_alignment_styles()
        self.viewport().update()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "_charge_colors"):
            self._refresh_color_formats()

    def _residue_border_rows(self):
        """Yield (document position, residue styles) without ruler/metal rows."""
        if hasattr(self, "_alignment_styles"):
            for line, row in self._visible_alignment_rows():
                yield (self._line_starts[line] + self._alignment_offset,
                       self._alignment_styles.get(row) or [])
        elif self._line_starts and self._sequence_length:
            line = 1 if len(self._line_starts) > 1 else 0
            # The paint loop indexes only visible residues. A slice here would
            # copy a potentially megabase-sized list on every scroll/repaint.
            yield (self._line_starts[line], self._residue_styles)

    def _visible_alignment_rows(self):
        """Yield sequence rows by visible document lines, excluding helper rows."""
        if not self._line_starts:
            return
        first = max(0, self.firstVisibleBlock().blockNumber())
        last = min(len(self._line_starts) - 1,
                   self.cursorForPosition(self.viewport().rect().bottomRight()).blockNumber())
        rows = self._residue_rows_by_line
        for line in range(first, last + 1):
            row = rows.get(line)
            if row is not None:
                yield line, row

    def _residue_guide_rows(self):
        """Visible sequence extents, including rows with no chemistry styles.

        Use declared lengths as well as text block lengths so overview rows stop
        at their own ends and alignment metal suffixes never acquire guides.
        """
        if not self._line_starts:
            return
        first_line = self.firstVisibleBlock().blockNumber()
        last_line = self.cursorForPosition(self.viewport().rect().bottomRight()).blockNumber()
        if hasattr(self, "_alignment_styles"):
            offset = self._alignment_offset
            entries = getattr(self, "entries_by_row", {})
            extents = ((line, entries.get(row, {}).get("length", self._alignment_length))
                       for line, row in self._visible_alignment_rows())
        else:
            offset = 0
            extents = [(1 if len(self._line_starts) > 1 else 0, self._sequence_length)]
        for line, length in extents:
            if line is None or not first_line <= line <= last_line or line >= len(self._line_starts):
                continue
            block = self.document().findBlockByNumber(line)
            length = min(int(length), block.length() - 1 - offset)
            if length >= 10:
                yield self._line_starts[line] + offset, length

    def _residue_guide_lines(self):
        """Separate groups of ten at cursor boundaries, never through letters."""
        viewport = self.viewport().rect()
        cursor = QTextCursor(self.document())
        lines = []
        for position, length in self._residue_guide_rows():
            cursor.setPosition(position)
            origin = self.cursorRect(cursor)
            if origin.bottom() < viewport.top() or origin.top() > viewport.bottom():
                continue
            y = origin.center().y()
            first = max(1, self.cursorForPosition(QPoint(viewport.left(), y)).position() - position - 1)
            last = min(length, self.cursorForPosition(QPoint(viewport.right(), y)).position() - position + 1)
            first_tick = ((first + 9) // 10) * 10
            for column in range(first_tick, last + 1, 10):
                cursor.setPosition(position + column)
                cell = self.cursorRect(cursor)
                x = cell.left() - 0.5
                if viewport.left() <= x <= viewport.right():
                    lines.append(QLineF(x, cell.top() + 1.0, x, cell.bottom() - 1.0))
        return lines

    def _residue_guide_color(self):
        color = QColor(self.palette().color(QPalette.ColorRole.Text))
        dark = self.palette().color(QPalette.ColorRole.Base).lightnessF() < 0.5
        color.setAlpha(80 if dark else 65)
        return color

    def _residue_border_rects(self):
        """Only inspect visible residues, even for megabase sequences.

        Adjacent residues with the same structure color share a thin frame.
        This leaves space for 12px letters without a grid between every base.
        QTextCursor supplies the same geometry used by click/drag selection.
        """
        if not (self._charge_colors or self._base_colors):
            return []
        rects = []
        viewport = self.viewport().rect()
        cursor = QTextCursor(self.document())
        for position, styles in self._residue_border_rows():
            if not styles:
                continue
            cursor.setPosition(position)
            origin = self.cursorRect(cursor)
            if origin.bottom() < viewport.top() or origin.top() > viewport.bottom():
                continue
            y = origin.center().y()
            first = max(0, self.cursorForPosition(QPoint(0, y)).position() - position - 1)
            last = min(len(styles), self.cursorForPosition(QPoint(viewport.right(), y)).position() - position + 2)
            if not hasattr(self, "_alignment_styles"):
                last = min(last, self._sequence_length)
            run_start, run_rgba = None, None

            def flush(end):
                if run_start is None:
                    return
                cursor.setPosition(position + run_start)
                left = self.cursorRect(cursor)
                cursor.setPosition(position + end)
                right = self.cursorRect(cursor)
                rect = QRectF(left.x() + 0.5, left.y() + 0.5,
                              max(0.0, right.x() - left.x() - 1.0), left.height() - 1.0)
                # Retain the exact structure RGB; keep hidden chains legible.
                color = QColor(*run_rgba[:3], max(160, min(230, run_rgba[3])))
                if not rect.isEmpty() and rect.intersects(QRectF(viewport)):
                    rects.append((rect, color))

            for index in range(first, last):
                style = styles[index]
                rgba = None
                if chemistry_color(style, charge=self._charge_colors, bases=self._base_colors,
                                   base_palette=self._base_palette):
                    value = style.get("rgba")
                    if value is not None and len(value) == 4:
                        rgba = tuple(int(c) for c in value)
                if rgba != run_rgba:
                    flush(index)
                    run_start = index if rgba is not None else None
                    run_rgba = rgba
            flush(last)
        return rects

    def paintEvent(self, event):
        super().paintEvent(event)
        guides = self._residue_guide_lines()
        rects = self._residue_border_rects()
        if not guides and not rects:
            return
        painter = QPainter(self.viewport())
        painter.setClipRegion(event.region())
        painter.setBrush(Qt.BrushStyle.NoBrush)
        guide_pen = QPen(self._residue_guide_color())
        guide_pen.setWidthF(1.0)
        painter.setPen(guide_pen)
        if guides:
            painter.drawLines(guides)
        for rect, color in rects:
            pen = QPen(color)
            pen.setWidthF(1.0)
            painter.setPen(pen)
            painter.drawRect(rect)
        painter.end()
