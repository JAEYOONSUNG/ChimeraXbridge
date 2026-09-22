"""Live chain controls inside the native Models tool."""

from Qt.QtCore import QPoint, Qt, QTimer
from Qt.QtWidgets import (
    QAbstractItemView, QHeaderView, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSizePolicy, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from .chain_visibility import ChainVisibilityController
from .compound_selection import CompoundSelectionWidget


class ModelChainControls(QWidget):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.controller = ChainVisibilityController(session)
        self.rows = {}
        self._keys = ()
        self._syncing = False
        self._closed = False
        self._handlers = []
        self._filter_expansion = None
        self._refresh_error = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._refresh_visible)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)

        self.compounds = CompoundSelectionWidget(session, self, preference_key="models")
        self.compounds.expand_button.setText("Molecules (non-protein)")
        self.compounds.selectionAboutToChange.connect(self._before_selection)
        layout.addWidget(self.compounds)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Find model, chain or molecule…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Find model or chain")
        self.search.textChanged.connect(self._filter_rows)
        layout.addWidget(self.search)
        hint = QLabel("Show toggles the whole chain. A dash means partly shown or selected.", self)
        hint.setWordWrap(True)
        hint.setProperty("role", "caption")
        layout.addWidget(hint)

        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Model / chain", "Show", "Select"])
        self.tree.setMinimumSize(0, 140)
        self.tree.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(12)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2):
            self.tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.tree.setColumnWidth(column, 50)
        self.tree.headerItem().setToolTip(1, "Show or hide atoms, cartoons and existing molecular surfaces.")
        self.tree.headerItem().setToolTip(2, "Add or remove this row's atoms from the scene selection.")
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.tree, 1)

        actions = QHBoxLayout()
        self.only_button = QPushButton("Only this", self)
        self.only_button.setToolTip("Show the highlighted row and hide other molecular chains. Undo restores the previous view.")
        self.only_button.clicked.connect(self._isolate)
        actions.addWidget(self.only_button)
        self.show_all_button = QPushButton("Show all chains", self)
        self.show_all_button.setToolTip("Show all open molecular chains, including rows hidden by the search filter.")
        self.show_all_button.clicked.connect(lambda: self._act(self.controller.show_all))
        actions.addWidget(self.show_all_button)
        layout.addLayout(actions)
        self.status = QLabel(self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setProperty("role", "caption")
        layout.addWidget(self.status)
        self._install_handlers()
        self.refresh()
        self.destroyed.connect(lambda *_args: self.cleanup())

    def _install_handlers(self):
        from chimerax.atomic import get_triggers
        from chimerax.core.models import (
            ADD_MODELS, REMOVE_MODELS, MODEL_DISPLAY_CHANGED,
            MODEL_NAME_CHANGED, MODEL_ID_CHANGED,
        )
        for name in (ADD_MODELS, REMOVE_MODELS, MODEL_DISPLAY_CHANGED,
                     MODEL_NAME_CHANGED, MODEL_ID_CHANGED, "selection changed", "command finished"):
            self._handlers.append(self.session.triggers.add_handler(name, self._queue_refresh))
        self._handlers.append(get_triggers().add_handler("changes done", self._queue_refresh))

    def _queue_refresh(self, *_args):
        if not self._closed and not self._timer.isActive():
            self._timer.start(0)

    def _refresh_visible(self):
        if not self._closed and self.isVisible():
            self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self._queue_refresh()

    def cleanup(self, *_args):
        if self._closed:
            return
        self._closed = True
        for handler in self._handlers:
            handler.remove()
        self._handlers = []
        try:
            self._timer.stop()
        except RuntimeError:
            pass
        self.controller.cleanup()

    def _before_selection(self):
        # Native selection notifications arrive on the next frame. Cancel any
        # queued display edit before it could apply to a newly selected chain.
        from Qt.QtWidgets import QApplication
        for widget in QApplication.allWidgets():
            if (type(widget).__name__ == "DisplayControlsWidget"
                    and getattr(widget, "session", None) is self.session):
                widget._selection_changed()

    def _row_atoms(self, item):
        from chimerax.atomic import Atoms, all_atomic_structures
        if item is None:
            return Atoms()
        model, chain_id = item._chain_key
        if not any(m is model for m in all_atomic_structures(self.session)):
            return Atoms()
        if chain_id is None:
            return model.atoms
        return model.residues.filter(model.residues.chain_ids == chain_id).atoms

    @staticmethod
    def _chain_description(residues):
        from chimerax.atomic import Residue
        types = set(residues.polymer_types)
        kinds = []
        if Residue.PT_PROTEIN in types:
            kinds.append("Protein")
        if Residue.PT_NUCLEIC in types:
            kinds.append("DNA/RNA")
        if not kinds:
            names = sorted(set(residues.names))
            kinds.append(", ".join(names[:4]) + (", …" if len(names) > 4 else ""))
        unit = "residue" if len(residues) == 1 else "residues"
        return " + ".join(kinds) + f" · {len(residues):,} {unit}"

    def refresh(self):
        if self._closed:
            return
        try:
            self._refresh_contents()
        except Exception as error:
            message = "Chain state unavailable: " + (str(error) or type(error).__name__)
            self.status.setText(message)
            if message != self._refresh_error:
                self.session.logger.warning(message)
            self._refresh_error = message
        else:
            if self._refresh_error and self.status.text() == self._refresh_error:
                self.status.setText("")
            self._refresh_error = ""

    def _refresh_contents(self):
        if self._closed:
            return
        from chimerax.atomic import all_atomic_structures
        models = sorted(all_atomic_structures(self.session), key=lambda m: m.id or ())
        entries = []
        for model in models:
            entries.append(((model, None), f"#{model.id_string} {model.name}", model.atoms))
            for _structure, chain_id, residues in model.residues.by_chain:
                label = f"{chain_id.strip() or '(blank)'} · {self._chain_description(residues)}"
                entries.append(((model, chain_id), label, residues.atoms))
        keys = tuple(key for key, _label, _atoms in entries)
        self._syncing = True
        blocked = self.tree.blockSignals(True)
        scroll = self.tree.verticalScrollBar().value()
        try:
            if keys != self._keys:
                expanded = {key: item.isExpanded() for key, item in self.rows.items()}
                current = getattr(self.tree.currentItem(), "_chain_key", None)
                self.tree.clear()
                self.rows = {}
                for key, label, atoms in entries:
                    parent = self.tree if key[1] is None else self.rows[(key[0], None)]
                    item = QTreeWidgetItem(parent, [label, "", ""])
                    item._chain_key = key
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setExpanded(expanded.get(key, True))
                    self.rows[key] = item
                    if key == current:
                        self.tree.setCurrentItem(item)
                self._keys = keys
            for key, label, atoms in entries:
                item = self.rows[key]
                item.setText(0, label)
                spec = f"#{key[0].id_string}" + (f"/{key[1]}" if key[1] is not None else "")
                item.setToolTip(0, f"{spec} {key[0].name}\n{label}\n{len(atoms):,} atoms")
                visible = self.controller.state(atoms) if len(atoms) else "hidden"
                states = {"shown": Qt.CheckState.Checked, "hidden": Qt.CheckState.Unchecked,
                          "mixed": Qt.CheckState.PartiallyChecked}
                item.setCheckState(1, states[visible])
                selected = atoms.selected if len(atoms) else []
                item.setCheckState(2, Qt.CheckState.Unchecked if not len(atoms) or not selected.any() else
                    Qt.CheckState.Checked if selected.all() else Qt.CheckState.PartiallyChecked)
                item.setToolTip(1, f"{visible.capitalize()} · toggle this row's atoms, cartoons and molecular surfaces.")
                item.setToolTip(2, "Toggle this row's scene selection; other selected objects are retained.")
            self._filter_rows()
            self.tree.verticalScrollBar().setValue(scroll)
        finally:
            self.tree.blockSignals(blocked)
            self._syncing = False
        self.compounds.refresh()
        self.show_all_button.setEnabled(any(m.num_atoms for m in models))
        if self.status.text() in ("", "Open a structure to control its chains."):
            self.status.setText("" if entries else "Open a structure to control its chains.")
        self._update_buttons()

    def _filter_rows(self, *_args):
        query = self.search.text().strip().casefold()
        if query and self._filter_expansion is None:
            self._filter_expansion = {key: item.isExpanded() for key, item in self.rows.items() if key[1] is None}
        exact_chain = bool(query and any(
            chain_id is not None and str(chain_id).casefold() == query for _model, chain_id in self.rows))
        for (model, chain_id), parent in self.rows.items():
            if chain_id is not None:
                continue
            parent_matches = not exact_chain and query in parent.text(0).casefold()
            matched = False
            for i in range(parent.childCount()):
                item = parent.child(i)
                visible = (str(item._chain_key[1]).casefold() == query if exact_chain else
                           parent_matches or query in item.text(0).casefold() or query in item.toolTip(0).casefold())
                item.setHidden(not visible)
                matched |= visible
            parent.setHidden(not (parent_matches or matched))
            if query and matched:
                parent.setExpanded(True)
            elif not query and self._filter_expansion is not None:
                parent.setExpanded(self._filter_expansion.get((model, None), parent.isExpanded()))
        if not query:
            self._filter_expansion = None
        self._update_buttons()

    def _current_item(self):
        item = self.tree.currentItem()
        if item is None or item.isHidden() or (item.parent() is not None and item.parent().isHidden()):
            return None
        return item

    def _update_buttons(self, *_args):
        self.only_button.setEnabled(bool(len(self._row_atoms(self._current_item()))))

    def _act(self, callback):
        if self._closed:
            return
        self.status.setText("")
        try:
            callback()
        except Exception as error:
            self.status.setText(str(error) or type(error).__name__)
            self.session.logger.warning("Chain controls: " + self.status.text())
        self._queue_refresh()

    def _item_changed(self, item, column):
        if self._syncing or self._closed or column not in (1, 2):
            return
        atoms = self._row_atoms(item)
        if not len(atoms):
            self._queue_refresh()
            return
        checked = item.checkState(column) != Qt.CheckState.Unchecked
        if column == 1:
            self._act(lambda: self.controller.set_visible(atoms, checked))
        else:
            from chimerax.core.objects import Objects
            from chimerax.std_commands.select import select_add, select_subtract
            self._before_selection()
            self._act(lambda: (select_add if checked else select_subtract)(
                self.session, Objects(atoms=atoms, bonds=atoms.intra_bonds)))

    def _isolate(self):
        atoms = self._row_atoms(self._current_item())
        if len(atoms):
            self._act(lambda: self.controller.isolate(atoms))


def install_model_chain_controls(panel):
    """Move native controls intact into an Advanced tab; install only once."""
    if hasattr(panel, "_codex_model_views"):
        old = panel._codex_chain_controls
        if type(old) is not ModelChainControls:
            # Native Models survives plugin reloads. Replace only our page,
            # leaving native model rows and actions in place.
            tabs = panel._codex_model_views
            current = tabs.currentIndex()
            query = old.search.text()
            old.cleanup()
            replacement = ModelChainControls(panel.session)
            replacement.search.setText(query)
            panel._codex_chain_controls = replacement
            tabs.removeTab(0)
            tabs.insertTab(0, replacement, "Chains && molecules")
            tabs.setCurrentIndex(current)
            old.deleteLater()
        panel.tool_window.fill_context_menu = panel._codex_chain_context_menu
        return panel._codex_chain_controls
    scroll = panel._codex_models_scroll
    native = scroll.takeWidget()
    tabs = QTabWidget()
    tabs.setMinimumSize(0, 0)
    tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
    chains = ModelChainControls(panel.session)
    tabs.addTab(chains, "Chains && molecules")
    tabs.addTab(native, "Advanced models")
    panel._codex_model_views = tabs
    panel._codex_chain_controls = chains
    scroll.setWidget(tabs)
    tabs.currentChanged.connect(lambda _index: panel._codex_chain_controls._queue_refresh())
    def context_menu(menu, x, y):
        if tabs.currentIndex() == 1:
            position = panel.tree.viewport().mapFrom(panel.tool_window.ui_area, QPoint(x, y))
            panel.fill_context_menu(menu, position.x(), position.y())
        else:
            chains = panel._codex_chain_controls
            menu.addAction("Show all chains", lambda: chains._act(chains.controller.show_all))
            menu.addAction("Toggle molecule selection", lambda: chains.compounds._toggle_selection(True))

    panel._codex_chain_context_menu = context_menu
    panel.tool_window.fill_context_menu = context_menu
    return chains
