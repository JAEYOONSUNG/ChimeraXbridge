"""Real Qt guide geometry/painting check in a disposable ChimeraX session.

Run with ChimeraX --nogui --notools --exit --script <this file>.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from Qt.QtCore import QPoint, Qt
from Qt.QtGui import QColor, QPalette, QTextCursor
from Qt.QtWidgets import QApplication, QVBoxLayout, QWidget
from PyQt6.QtTest import QTest

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "chimerax.codex_bridge", root / "src/__init__.py",
    submodule_search_locations=[str(root / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from chimerax.codex_bridge.sequence_bar import (
    ClickableAlignmentText, ClickableChainOverviewText, ClickableSequenceText,
    _alignment_ruler, _tick_line,
)

app = QApplication.instance() or QApplication([])
SOURCE = (60, 128, 94, 255)
letters = "KRDEHACGTU" * 8

for ruler in (_tick_line, _alignment_ruler):
    for length in (0, 9, 10, 23, 80, 100):
        text = ruler(length)
        assert len(text) == length
        for tick in range(10, length + 1, 10):
            assert text[tick - len(str(tick)):tick] == str(tick), (ruler.__name__, length, tick, text)


def styles(sequence):
    return [{"rgba": SOURCE, "letter": letter, "residue_name": "LYS" if letter == "K" else letter,
             "polymer_kind": "nucleic" if letter in "ACGTU" else "protein"}
            for letter in sequence]


def position(view, line, column=0):
    return view.document().findBlockByNumber(line).position() + column


def cell(view, document_position):
    cursor = QTextCursor(view.document())
    cursor.setPosition(document_position)
    return view.cursorRect(cursor)


def guide_columns(view, line, offset=0):
    """Read actual guide x coordinates back against Qt's text cursor geometry."""
    start = position(view, line, offset)
    first = cell(view, start)
    result = []
    for guide in view._residue_guide_lines():
        if abs(guide.y1() - first.top() - 1) > 0.1:
            continue
        result.append(view.cursorForPosition(QPoint(round(guide.x1() + 0.5), first.center().y())).position() - start)
        expected = cell(view, start + result[-1])
        assert abs(guide.x1() - expected.left() + 0.5) < 0.01
        assert guide.y1() == expected.top() + 1 and guide.y2() == expected.bottom() - 1
    return result


clicks = []
single = ClickableSequenceText(lambda *args: clicks.append(args), lambda *args: None)
single.set_sequence_text([_tick_line(len(letters)), letters + "   Zn:1"], len(letters), [""] * len(letters), styles(letters))
single.highlight_ranges([(12, 14)])
single.set_search_ranges([(23, 25)], 0)

aligned = ClickableAlignmentText(lambda *args: None)
aligned.set_alignment_text(
    "Ref     " + _alignment_ruler(80) + "\nRef     " + letters + "   Zn:1\nOther   " + "-" * 80,
    80, {"reference": styles(letters), "moving": [None] * 80},
    {"offset": 8, "row_lines": {"ruler": 0, "reference": 1, "moving": 2},
     "sequence_rows": ["reference", "moving"]})
overview = ClickableChainOverviewText(lambda *args: None, lambda *args: None, lambda *args: None)
overview.set_chain_rows([
    {"spec": "#90/long", "sequence": letters, "length": len(letters), "residue_styles": styles(letters)},
    {"spec": "#90/short", "sequence": letters[:23], "length": 23},
    {"spec": "#90/tiny", "sequence": "ACGTU", "length": 5},
])
window = QWidget()
layout = QVBoxLayout(window)
for view in (single, aligned, overview):
    layout.addWidget(view)
window.resize(1050, 360)
window.show()
app.processEvents()

for charge, bases in ((False, False), (True, True)):
    for view in (single, aligned, overview):
        text_before = view.toPlainText()
        view.set_residue_coloring(charge=charge, bases=bases)
        app.processEvents()
        assert view.toPlainText() == text_before
        assert view._residue_guide_lines(), (type(view).__name__, charge, bases)
    assert guide_columns(single, 1) == list(range(10, 81, 10))
    assert guide_columns(single, 0) == [], "Ruler must not be crossed by guides"
    assert guide_columns(aligned, 1, 8) == list(range(10, 81, 10))
    assert guide_columns(aligned, 2, 8) == list(range(10, 81, 10)), "Alignment gaps retain column guides"
    assert guide_columns(overview, 2, overview._alignment_offset) == [10, 20], "Unstyled short row must stop at its own end"
    assert guide_columns(overview, 3, overview._alignment_offset) == []
    for dark in (False, True):
        palette = QPalette(app.palette())
        for role, light_value, dark_value in (
            (QPalette.ColorRole.Base, "#ffffff", "#252525"),
            (QPalette.ColorRole.Text, "#202020", "#eeeeee"),
            (QPalette.ColorRole.Window, "#eeeeee", "#353535"),
        ):
            palette.setColor(role, QColor(dark_value if dark else light_value))
        app.setPalette(palette)
        app.processEvents()
        for view in (single, aligned, overview):
            assert view._residue_guide_color().alpha() >= 65
            with_guides = view.viewport().grab().toImage()
            original = view._residue_guide_lines
            try:
                view._residue_guide_lines = lambda: []
                without_guides = view.viewport().grab().toImage()
            finally:
                view._residue_guide_lines = original
            assert with_guides != without_guides, (type(view).__name__, dark, charge, "guides not painted")
        window.grab().save(f"/tmp/sequence-guides-{'dark' if dark else 'light'}-{'filled' if charge else 'plain'}.png")

# The guides are paint-only: a click on residue 11 still maps to index 10.
index = 10
rect = cell(single, position(single, 1, index))
QTest.mouseClick(single.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                 QPoint(rect.left() + 1, rect.center().y()))
assert clicks[-1][:2] == (index, index), clicks
assert single._search_ranges == [(23, 25)]

# Long rows exercise horizontal clipping; many rows exercise vertical clipping.
long_letters = "ACGTU" * 4000
overview.set_chain_rows([
    {"spec": "#91/long", "sequence": long_letters, "length": len(long_letters)},
    {"spec": "#91/short", "sequence": letters[:23], "length": 23},
] + [{"spec": f"#91/{index}", "sequence": letters, "length": len(letters)} for index in range(40)])
app.processEvents()
scrollbar = overview.horizontalScrollBar()
before = guide_columns(overview, 1, overview._alignment_offset)
assert before[0] == 10
scrollbar.setValue(3000)
app.processEvents()
after = guide_columns(overview, 1, overview._alignment_offset)
assert after and after[0] > before[-1]
assert guide_columns(overview, 2, overview._alignment_offset) == []
assert len(overview._residue_guide_lines()) <= 20, "Guide work must be bounded by visible columns"
scrollbar.setValue(scrollbar.maximum())
app.processEvents()
assert all(g.x1() >= 0 for g in overview._residue_guide_lines())
scrollbar.setValue(0)
overview.verticalScrollBar().setValue(overview.verticalScrollBar().maximum())
app.processEvents()
visible = overview.viewport().rect()
assert all(g.y2() >= visible.top() and g.y1() <= visible.bottom() for g in overview._residue_guide_lines())
assert len(list(overview._residue_guide_rows())) < 10, "Offscreen chain rows must not be inspected"

report = {"ok": True, "step": 10, "views": ["single", "alignment", "all chains"],
          "light_dark": True, "with_without_fills": True, "short_unstyled_rows": True,
          "scroll_clipping": True, "picking_and_search": True}
Path("/tmp/sequence-guides-check.json").write_text(json.dumps(report, indent=2))
print("SEQUENCE_GUIDES_OK", json.dumps(report))
window.close()
