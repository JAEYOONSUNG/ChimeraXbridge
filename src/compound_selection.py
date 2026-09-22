"""Compound selection controls for the scrollable display inspector."""

from Qt.QtCore import Qt, Signal
from Qt.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton,
    QVBoxLayout, QWidget,
)


def compound_atoms(session, *, all_nonprotein=False):
    """Return molecular atoms only, independent of the current selection."""
    from chimerax.atomic import Atoms, Residue, all_atomic_structures, concatenate

    collections = []
    for structure in all_atomic_structures(session):
        atoms = structure.atoms
        if not len(atoms):
            continue
        polymer_types = atoms.residues.polymer_types
        if all_nonprotein:
            mask = polymer_types != Residue.PT_PROTEIN
        else:
            # Standalone compounds can be categorized as "main", so the
            # ligand/ions selectors alone do not cover this target.
            mask = ((polymer_types == Residue.PT_NONE)
                    & (atoms.structure_categories != "solvent"))
        collections.append(atoms.filter(mask))
    return concatenate(collections, Atoms)


class CompoundSelectionWidget(QWidget):
    """A collapsible section whose button mirrors the live molecular selection."""

    selectionAboutToChange = Signal()

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.expand_button = QToolButton(self)
        self.expand_button.setText("Compounds")
        self.expand_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.expand_button.setCheckable(True)
        self.expand_button.setChecked(True)
        self.expand_button.setArrowType(Qt.ArrowType.DownArrow)
        self.expand_button.setToolTip("Expand or collapse compound selection controls.")
        self.expand_button.toggled.connect(self._set_expanded)
        layout.addWidget(self.expand_button)

        self.body = QWidget(self)
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(4)
        layout.addWidget(self.body)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.mode_combo = QComboBox(self.body)
        self.mode_combo.addItem("Compounds", False)
        self.mode_combo.addItem("All nonprotein", True)
        self.mode_combo.setAccessibleName("Compound selection scope")
        self.mode_combo.setToolTip("Choose the target; changing this option does not change the selection.")
        self.mode_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.mode_combo.currentIndexChanged.connect(self.refresh)
        row.addWidget(self.mode_combo, 1)
        self.select_button = QPushButton("Select compounds", self.body)
        self.select_button.setCheckable(True)
        self.select_button.setAccessibleName("Select nonprotein compounds")
        self.select_button.clicked.connect(self._toggle_selection)
        row.addWidget(self.select_button)
        body.addLayout(row)
        self.scope_label = QLabel(self.body)
        self.scope_label.setObjectName("Caption")
        self.scope_label.setWordWrap(True)
        body.addWidget(self.scope_label)
        self.count_label = QLabel(self.body)
        self.count_label.setObjectName("Caption")
        self.count_label.setWordWrap(True)
        body.addWidget(self.count_label)
        self.refresh()

    def _set_expanded(self, expanded):
        self.body.setVisible(expanded)
        self.expand_button.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def _targets(self):
        return compound_atoms(self.session, all_nonprotein=bool(self.mode_combo.currentData()))

    def refresh(self, *_args):
        atoms = self._targets()
        count = len(atoms)
        selected = int(atoms.selected.sum()) if count else 0
        all_selected = bool(count and selected == count)
        blocked = self.select_button.blockSignals(True)
        try:
            self.select_button.setChecked(all_selected)
        finally:
            self.select_button.blockSignals(blocked)
        self.select_button.setEnabled(bool(count))
        self.select_button.setText("Deselect compounds" if all_selected else "Select compounds")
        self.select_button.setToolTip(
            "Remove these atoms from the selection; keep any other selected objects."
            if all_selected else
            "Replace the current selection with these atoms from all open structures. Undo restores the previous selection.")
        self.scope_label.setText(
            "All open structures · includes DNA/RNA and solvent; excludes protein."
            if self.mode_combo.currentData() else
            "All open structures · excludes protein, DNA/RNA and solvent.")
        if count:
            state = "Partly selected · " if 0 < selected < count else ""
            self.count_label.setText(
                f"{state}{selected:,} / {count:,} atoms selected · {len(atoms.unique_residues):,} residues")
        else:
            self.count_label.setText("No matching compounds in the open structures.")
            self.select_button.setToolTip("Open a structure containing matching nonprotein atoms.")

    def _toggle_selection(self, _checked):
        from chimerax.core.objects import Objects
        from chimerax.std_commands.select import select, select_subtract

        # Resolve again at click time, so a pending refresh cannot leave stale
        # atoms or a closed structure in the action target.
        atoms = self._targets()
        try:
            if len(atoms):
                self.selectionAboutToChange.emit()
                objects = Objects(atoms=atoms, bonds=atoms.intra_bonds)
                action = select_subtract if atoms.selected.all() else select
                action(self.session, objects)
        finally:
            self.refresh()
