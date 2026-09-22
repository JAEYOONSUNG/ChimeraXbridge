"""Compound selection controls for the scrollable display inspector."""

from Qt.QtCore import Qt, Signal
from Qt.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton,
    QVBoxLayout, QWidget,
)
from chimerax.core.settings import Settings


class CompoundSelectionSettings(Settings):
    AUTO_SAVE = {"all_nonprotein": False, "expanded": True}


def _settings(session, preference_key="default"):
    attribute = ("_codex_models_compound_selection_settings" if preference_key == "models"
                 else "_codex_compound_selection_settings")
    settings = getattr(session, attribute, None)
    if settings is None:
        name = "Codex Models Molecules" if preference_key == "models" else "Codex Compound Selection"
        settings = CompoundSelectionSettings(session, name)
        setattr(session, attribute, settings)
    return settings


def compound_atoms(session, *, all_nonprotein=False, model=None):
    """Return molecular atoms only, independent of the current selection."""
    from chimerax.atomic import Atoms, Residue, all_atomic_structures, concatenate

    collections = []
    for structure in all_atomic_structures(session):
        if model is not None and structure is not model:
            continue
        residues = structure.residues
        polymer_types = residues.polymer_types
        if all_nonprotein:
            atoms = residues.filter(polymer_types != Residue.PT_PROTEIN).atoms
        else:
            # Standalone compounds can be categorized as "main", so the
            # ligand/ions selectors alone do not cover this target.
            atoms = residues.filter(polymer_types == Residue.PT_NONE).atoms
            atoms = atoms.filter(atoms.structure_categories != "solvent")
        if len(atoms):
            collections.append(atoms)
    return concatenate(collections, Atoms)


def _molecular_pseudobonds(atoms):
    """Include molecular connections, without selecting distance annotations."""
    from chimerax.atomic import Structure
    import numpy as np

    pseudobonds = atoms.intra_pseudobonds
    names = {Structure.PBG_METAL_COORDINATION, Structure.PBG_MISSING_STRUCTURE}
    mask = np.fromiter((group.name in names for group in pseudobonds.groups),
                       dtype=bool, count=len(pseudobonds))
    return pseudobonds.filter(mask)


class CompoundSelectionWidget(QWidget):
    """A collapsible section whose button mirrors the live molecular selection."""

    selectionAboutToChange = Signal()

    def __init__(self, session, parent=None, *, preference_key="default"):
        super().__init__(parent)
        self.session = session
        self._settings = _settings(session, preference_key)
        self._target_attribute = ("_codex_models_compound_target_model" if preference_key == "models"
                                  else "_codex_compound_target_model")
        self._select_label = "Select only" if preference_key == "models" else "Select"
        self._action_error = ""
        self._selection_in_progress = False
        self._target_model = getattr(session, self._target_attribute, None)
        self._target_present = True
        self._model_signature = None
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        # Let these controls grow with the user's font instead of inheriting
        # the parent inspector's fixed 22-pixel content-height cap.
        self.setStyleSheet("QComboBox, QPushButton { max-height: 1000px; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.expand_button = QToolButton(self)
        self.expand_button.setText("Compounds")
        self.expand_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.expand_button.setCheckable(True)
        self.expand_button.setChecked(bool(self._settings.expanded))
        self.expand_button.setArrowType(Qt.ArrowType.DownArrow)
        self.expand_button.setToolTip("Expand or collapse compound selection controls.")
        self.expand_button.toggled.connect(self._set_expanded)
        layout.addWidget(self.expand_button)

        self.body = QWidget(self)
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(4)
        layout.addWidget(self.body)
        model_row = QHBoxLayout()
        model_row.setSpacing(4)
        model_label = QLabel("Models", self.body)
        model_row.addWidget(model_label)
        self.model_combo = QComboBox(self.body)
        model_label.setBuddy(self.model_combo)
        self.model_combo.setAccessibleName("Compound target model")
        self.model_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.model_combo.currentIndexChanged.connect(self._model_changed)
        model_row.addWidget(self.model_combo, 1)
        body.addLayout(model_row)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.mode_combo = QComboBox(self.body)
        self.mode_combo.addItem("Compounds", False)
        self.mode_combo.addItem("All nonprotein", True)
        self.mode_combo.setCurrentIndex(1 if self._settings.all_nonprotein else 0)
        self.mode_combo.setAccessibleName("Molecule types to select")
        self.mode_combo.setToolTip("Choose the target; changing this option does not change the selection.")
        self.mode_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        row.addWidget(self.mode_combo, 1)
        self.select_button = QPushButton("Select compounds", self.body)
        self.select_button.setCheckable(True)
        self.select_button.setAccessibleName("Select nonprotein compounds")
        self.select_button.clicked.connect(self._toggle_selection)
        row.addWidget(self.select_button)
        body.addLayout(row)
        self.scope_label = QLabel(self.body)
        self.scope_label.setObjectName("Caption")
        self.scope_label.setTextFormat(Qt.TextFormat.PlainText)
        self.scope_label.setWordWrap(True)
        body.addWidget(self.scope_label)
        self.count_label = QLabel(self.body)
        self.count_label.setObjectName("Caption")
        self.count_label.setTextFormat(Qt.TextFormat.PlainText)
        self.count_label.setWordWrap(True)
        body.addWidget(self.count_label)
        self.error_label = QLabel(self.body)
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        body.addWidget(self.error_label)
        self._set_expanded(self.expand_button.isChecked())

    def _set_expanded(self, expanded):
        self.body.setVisible(expanded)
        self._settings.expanded = bool(expanded)
        self.expand_button.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.refresh()

    def _mode_changed(self, *_args):
        self._settings.all_nonprotein = bool(self.mode_combo.currentData())
        self._action_error = ""
        self.refresh()

    def _targets(self):
        return compound_atoms(self.session, all_nonprotein=bool(self.mode_combo.currentData()),
                              model=self._target_model)

    def _model_changed(self, *_args):
        self._target_model = self.model_combo.currentData()
        setattr(self.session, self._target_attribute, self._target_model)
        self._action_error = ""
        self.refresh()

    def _sync_model_choices(self):
        from chimerax.atomic import all_atomic_structures

        models = sorted(all_atomic_structures(self.session), key=lambda m: m.id or ())
        self._target_present = self._target_model is None or any(m is self._target_model for m in models)
        choices = [("All open structures", None)]
        choices.extend((f"#{m.id_string} {m.name}", m) for m in models)
        if not self._target_present:
            choices.append(("Model no longer open — choose another", self._target_model))
        signature = tuple((label, id(model)) for label, model in choices)
        if signature != self._model_signature:
            blocked = self.model_combo.blockSignals(True)
            try:
                self.model_combo.clear()
                for label, model in choices:
                    self.model_combo.addItem(label, model)
                self.model_combo.setCurrentIndex(next(
                    i for i, (_label, model) in enumerate(choices) if model is self._target_model))
            finally:
                self.model_combo.blockSignals(blocked)
            self._model_signature = signature
        self.model_combo.setToolTip(self.model_combo.currentText() + " (including hidden atoms).")

    def refresh(self, *_args, _atoms=None):
        target_name = "nonprotein atoms" if self.mode_combo.currentData() else "compounds"
        try:
            self._sync_model_choices()
            if not self.expand_button.isChecked():
                self.expand_button.setToolTip(
                    f"{self.mode_combo.currentText()} · {self.model_combo.currentText()}. Expand to refresh selection counts.")
                return
            self.expand_button.setToolTip("Collapse compound selection controls.")
            atoms = self._targets() if _atoms is None else _atoms
        except Exception as error:
            self.select_button.setEnabled(False)
            self.select_button.setChecked(False)
            self.count_label.setText("Selection unavailable. Refresh to try again.")
            self._show_error(f"Could not inspect {target_name}: {error}")
            return
        self._show_error(self._action_error)
        count = len(atoms)
        selected = int(atoms.selected.sum()) if count else 0
        all_selected = bool(count and selected == count)
        blocked = self.select_button.blockSignals(True)
        try:
            self.select_button.setChecked(all_selected)
        finally:
            self.select_button.blockSignals(blocked)
        self.select_button.setEnabled(bool(count) and not self._selection_in_progress)
        action_name = "Deselect" if all_selected else self._select_label
        self.select_button.setText(action_name)
        self.select_button.setAccessibleName(f"{action_name} {target_name}")
        model_scope = ("All open structures" if self._target_model is None else
                       "Model no longer open" if not self._target_present else
                       f"Model #{self._target_model.id_string}")
        self.select_button.setToolTip(
            "Remove these atoms from the selection; keep any other selected objects."
            if all_selected else
            f"Replace the current selection with {target_name} from {model_scope.lower()}. Undo restores the previous selection.")
        self.scope_label.setText(model_scope + " (including hidden) · " + (
            "includes DNA/RNA and solvent; excludes protein." if self.mode_combo.currentData() else
            "excludes protein, DNA/RNA and solvent."))
        if count:
            models = atoms.unique_structures
            residue_count = len(atoms.unique_residues)
            residue_unit = "residue" if residue_count == 1 else "residues"
            model_unit = "model" if len(models) == 1 else "models"
            state = "Partly selected · " if 0 < selected < count else ""
            self.count_label.setText(
                f"{state}{selected:,} / {count:,} atoms selected · {residue_count:,} {residue_unit} · {len(models):,} {model_unit}")
            names = [f"#{m.id_string} {m.name}" for m in list(models)[:8]]
            if len(models) > 8:
                names.append(f"… and {len(models) - 8:,} more")
            self.count_label.setToolTip("Matching structures:\n" + "\n".join(names))
        else:
            self.count_label.setText(
                f"No matching {target_name} in this model scope." if self._target_present else
                "Target model is no longer open. Choose another model.")
            self.count_label.setToolTip("")
            self.select_button.setToolTip(
                "Open a structure containing matching nonprotein atoms." if self._target_present else
                "Choose another model explicitly; the target will not switch to all structures.")
        self.select_button.setAccessibleDescription(
            self.scope_label.text() + " " + self.count_label.text())

    def _show_error(self, message):
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))

    def _toggle_selection(self, _checked):
        if self._selection_in_progress:
            return
        from chimerax.core.objects import Objects
        from chimerax.std_commands.select import select, select_subtract

        # Resolve again at click time, so a pending refresh cannot leave stale
        # atoms or a closed structure in the action target.
        self._action_error = ""
        self._selection_in_progress = True
        self.select_button.setEnabled(False)
        atoms = None
        try:
            atoms = self._targets()
            if len(atoms):
                original_scope = (self._target_model, bool(self.mode_combo.currentData()))
                self.selectionAboutToChange.emit()
                if (self._target_model is not original_scope[0]
                        or bool(self.mode_combo.currentData()) != original_scope[1]):
                    atoms = None
                    self._action_error = "Target changed. Review the scope and select again."
                    return
                # Other synchronous callbacks may close a model or edit atoms.
                # Use the surviving target and never act on the old collection.
                atoms = self._targets()
                if not len(atoms):
                    return
                objects = Objects(atoms=atoms, bonds=atoms.intra_bonds,
                                  pseudobonds=_molecular_pseudobonds(atoms))
                action = select_subtract if atoms.selected.all() else select
                action(self.session, objects)
        except Exception as error:
            self._action_error = f"Could not change selection: {error}"
            self.session.logger.warning(self._action_error)
        finally:
            self._selection_in_progress = False
            self.refresh(_atoms=atoms)
