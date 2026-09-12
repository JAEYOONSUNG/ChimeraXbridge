import json
from urllib.request import Request, urlopen


_LOG_FONT_PATCH_VERSION = 1
_AI_TOOLBAR_STYLE_VERSION = 24
_MODEL_PANEL_ID_REORDER_PATCH_VERSION = 2
# Keep artwork and label baselines consistent; long names get enough width
# instead of clipping into the next tool. Section overflow remains native Qt.
_AI_ICON_PX = 28
_AI_BUTTON_W = 48
_AI_BUTTON_H = 60
_AI_FONT_PX = 11
_AI_SECTION_TOP_PX = 6
_AI_SECTION_BOTTOM_PX = 3

_AI_TOOLBAR_BUTTON_TITLES = {
    "Icons", "Original icons", "New SVG icons",
    "Analyze",
    "View",
    "Site",
    "Binding Site",
    "Pocket",
    "Zoom",
    "Cavity",
    "Figure",
    "Blast",
    "AlphaFold",
    "Similar",
    "FoldMason",
    "FoldDisco",
    "NucDock",
    "AF Complex",
    "Profile",
    "Consurf",
    "Conserve",
    "SignalP",
    "Boltz",
    "RAPiDock",
    "HPEPDOCK",
    "HHpred",
    "Catalytic",
    "Interface",
    "Membrane",
    "PISA",
    "Metal",
    "Metals",
    "CAVER",
    "DALI",
    "VAST",
    "PDBeFold",
    "US-align",
    "RMSD",
    "StructMSA",
    "Struct MSA",
    "Display Ctrl",
    "Sequence",
    "Action Pad",
    "Bookmarks",
    "AI",
    "MLP",
    "MD",
    "PyRosetta",
    "Energy",
}

_AI_TOOLBAR_DISPLAY_TEXT = {
    "Original icons": "Original\nicons",
    "New SVG icons": "New SVG\nicons",
    "AF Complex": "AF\nComplex",
    "Action Pad": "Action\nPad",
    "Display Ctrl": "Display\nCtrl",
    "StructMSA": "Struct\nMSA",
    "Struct MSA": "Struct\nMSA",
}

_AI_TOOLBAR_SECTION_TITLES = {"Quick", "Sequence", "Modeling", "Sites", "Channels", "Structure", "AI Tools", "Adjust", "Helper"}


def _fixed_size_policy(QSizePolicy):
    try:
        return QSizePolicy.Policy.Fixed
    except Exception:
        return QSizePolicy.Fixed

_LOG_MONO_CSS = """
/* codex-bridge-log-font */
body,
p,
div,
span,
a,
table,
tbody,
thead,
tr,
td,
th,
pre,
code,
.cxcmd,
.cxcmd_as_doc,
.cxcmd_as_cmd {
    font-family: Menlo, Monaco, "SF Mono", Consolas, "Courier New", monospace !important;
    font-size: 11px !important;
    line-height: 1.32 !important;
}
"""


def apply_runtime_patches(session):
    style_builtin_log(session)
    _relax_codex_dock_widths(session)
    _patch_tabbedtoolbar_section()
    _patch_model_panel_id_reorder(session)
    _patch_model_panel_ui(session)
    try:
        from .auto_reload import install_auto_reload_watcher

        install_auto_reload_watcher(session)
    except Exception:
        pass
    if getattr(session, "_codex_bridge_runtime_patches_applied", False):
        return
    session._codex_bridge_runtime_patches_applied = True
    _patch_blastprotein_pdbinfo()


def _patch_model_panel_ui(session):
    from chimerax.model_panel.tool import ModelPanel
    from .ui_theme import style_model_panel, update_model_panel_columns

    if getattr(ModelPanel, "_codex_compact_ui_patched", 0) != 2:
        original_init = getattr(ModelPanel, "_codex_compact_original_init", ModelPanel.__init__)
        original_fill = getattr(ModelPanel, "_codex_compact_original_fill", ModelPanel._fill_tree)

        def initialize(panel, *args, **kwargs):
            original_init(panel, *args, **kwargs)
            from .ui_theme import style_model_panel
            style_model_panel(panel)

        def fill(panel, *args, **kwargs):
            selected = {getattr(item, "_model", None) for item in panel.tree.selectedItems()}
            current = getattr(panel.tree.currentItem(), "_model", None)
            result = original_fill(panel, *args, **kwargs)
            if getattr(panel, "_codex_compact_models_layout", False):
                # Native full rebuilds retain expansion but drop highlighted
                # rows. Preserve model identity through delayed frame refreshes.
                from Qt.QtCore import QSignalBlocker, QItemSelectionModel
                from .ui_theme import update_model_panel_columns
                blocker = QSignalBlocker(panel.tree)
                for item in panel._items:
                    model = getattr(item, "_model", None)
                    if model is not None and model in selected:
                        item.setSelected(True)
                    if model is not None and model is current:
                        panel.tree.setCurrentItem(item, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
                del blocker
                update_model_panel_columns(panel)
            return result

        ModelPanel.__init__ = initialize
        ModelPanel._fill_tree = fill
        ModelPanel._codex_compact_original_init = original_init
        ModelPanel._codex_compact_original_fill = original_fill
        ModelPanel._codex_compact_ui_patched = 2
    tools = getattr(session, "tools", None)
    for tool in tools.list() if tools is not None else ():
        if isinstance(tool, ModelPanel):
            style_model_panel(tool)


def _patch_model_panel_id_reorder(session=None):
    try:
        from chimerax.model_panel import tool as model_panel_tool
    except Exception:
        return
    panel_cls = getattr(model_panel_tool, "ModelPanel", None)
    if panel_cls is None:
        return
    if getattr(panel_cls, "_codex_bridge_id_reorder_patch_version", 0) == _MODEL_PANEL_ID_REORDER_PATCH_VERSION:
        _patch_open_model_panel_instances(session, panel_cls)
        return

    original_change = getattr(
        panel_cls,
        "_codex_bridge_original_tree_change_cb",
        panel_cls._tree_change_cb,
    )
    original_menu = getattr(
        panel_cls,
        "_codex_bridge_original_fill_context_menu",
        panel_cls.fill_context_menu,
    )

    def patched_tree_change_cb(self, item, column):
        if column != getattr(self, "ID_COLUMN", 1):
            return original_change(self, item, column)
        try:
            model = self.models[self._items.index(item)]
        except Exception:
            return original_change(self, item, column)
        try:
            from .model_order import reorder_top_model_to_id

            id_text = item.text(self.ID_COLUMN)
            self.self_initiated = True
            message = reorder_top_model_to_id(self.session, model, id_text)
            try:
                self.session.logger.status(message)
                self.session.logger.info(message)
            except Exception:
                pass
            self._initiate_fill_tree(always_rebuild=True)
        except Exception as err:
            try:
                self._initiate_fill_tree(always_rebuild=True)
            except Exception:
                pass
            from chimerax.core.errors import UserError

            raise UserError(str(err) if str(err) else err.__class__.__name__)

    def patched_fill_context_menu(self, menu, x, y):
        result = original_menu(self, menu, x, y)
        try:
            item = self.tree.itemAt(x, y)
            selected_items = list(self.tree.selectedItems() or [])
            if item is not None and item not in selected_items:
                selected_items = [item]
            candidate_models = []
            for selected_item in selected_items:
                try:
                    model = self.models[self._items.index(selected_item)]
                except Exception:
                    continue
                if getattr(model, "id", None) and len(model.id) == 1:
                    candidate_models.append(model)
            if len(candidate_models) != 1:
                return result

            model = candidate_models[0]

            def move_model_id():
                try:
                    from Qt.QtWidgets import QInputDialog

                    current = str(getattr(model, "id_string", "") or "").strip()
                    text, ok = QInputDialog.getText(
                        self.tool_window.ui_area,
                        "Move model ID",
                        f"Move #{current} to ID:",
                        text=current,
                    )
                    if not ok:
                        return
                    from .model_order import reorder_top_model_to_id

                    message = reorder_top_model_to_id(self.session, model, text)
                    try:
                        self.session.logger.status(message)
                        self.session.logger.info(message)
                    except Exception:
                        pass
                    self._initiate_fill_tree(always_rebuild=True)
                except Exception as err:
                    try:
                        self.session.logger.error(str(err) if str(err) else err.__class__.__name__)
                    except Exception:
                        pass

            try:
                from Qt.QtGui import QAction

                menu.addSeparator()
                action = QAction("Move ID...", menu)
                action.triggered.connect(lambda _checked=False: move_model_id())
                menu.addAction(action)
            except Exception:
                pass
        except Exception:
            pass
        return result

    panel_cls._tree_change_cb = patched_tree_change_cb
    panel_cls.fill_context_menu = patched_fill_context_menu
    panel_cls._codex_bridge_original_tree_change_cb = original_change
    panel_cls._codex_bridge_original_fill_context_menu = original_menu
    panel_cls._codex_bridge_id_reorder_patch_version = _MODEL_PANEL_ID_REORDER_PATCH_VERSION
    _patch_open_model_panel_instances(session, panel_cls)


def _patch_open_model_panel_instances(session, panel_cls):
    if session is None:
        return
    try:
        panels = [
            tool
            for tool in session.tools.list()
            if isinstance(tool, panel_cls) or getattr(tool, "tool_name", "") == "Model Panel"
        ]
    except Exception:
        panels = []
    for panel in panels:
        if getattr(panel, "_codex_bridge_instance_id_reorder_patch_version", 0) == _MODEL_PANEL_ID_REORDER_PATCH_VERSION:
            continue
        try:
            panel.tool_window.fill_context_menu = panel.fill_context_menu
        except Exception:
            pass
        try:
            panel.tree.itemChanged.disconnect()
        except Exception:
            pass
        try:
            panel.tree.itemChanged.connect(panel._tree_change_cb)
        except Exception:
            pass
        try:
            panel._initiate_fill_tree(always_rebuild=True)
        except Exception:
            pass
        panel._codex_bridge_instance_id_reorder_patch_version = _MODEL_PANEL_ID_REORDER_PATCH_VERSION


def style_builtin_log(session=None):
    _patch_builtin_log_css()
    if session is not None:
        _refresh_open_builtin_log(session)


def _relax_codex_dock_widths(session):
    main_window = getattr(getattr(session, "ui", None), "main_window", None)
    if main_window is None:
        return
    try:
        from Qt.QtWidgets import QDockWidget, QWidget
    except Exception:
        return

    dock_tokens = ("ai assistant", "display controls", "sequence bar", "action pad", "models", "model panel")
    for dock_widget in main_window.findChildren(QDockWidget):
        try:
            title = str(dock_widget.windowTitle() or dock_widget.objectName() or "").lower()
        except Exception:
            title = ""
        if not any(token in title for token in dock_tokens):
            continue
        widgets = [dock_widget]
        try:
            root = dock_widget.widget()
        except Exception:
            root = None
        if root is not None:
            widgets.append(root)
            try:
                widgets.extend(root.findChildren(QWidget))
            except Exception:
                pass
        for widget in tuple(dict.fromkeys(widgets)):
            try:
                widget.setMaximumWidth(16777215)
            except Exception:
                pass


def _style_ai_button_widget(button):
    """Shared toolbar/overflow geometry, measured using the actual label font."""
    from Qt.QtCore import QSize, Qt
    from Qt.QtGui import QFont, QFontMetrics
    from Qt.QtWidgets import QSizePolicy

    title = " ".join(str(button.text() or "").split())
    if title not in _AI_TOOLBAR_BUTTON_TITLES:
        return
    display_text = _AI_TOOLBAR_DISPLAY_TEXT.get(title, title)
    # Qt centers the combined icon/label block. Reserve the same two text
    # lines on every button so single-line labels cannot lower their icons.
    if "\n" not in display_text:
        display_text += "\n "
    button.setText(display_text)
    button.setAccessibleName(title)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
    button.setIconSize(QSize(_AI_ICON_PX, _AI_ICON_PX))
    font = button.font()
    font.setPixelSize(_AI_FONT_PX)
    font.setWeight(QFont.Weight.Medium)
    button.setFont(font)
    metrics = QFontMetrics(font)
    width = max(_AI_BUTTON_W, max(metrics.horizontalAdvance(line) for line in display_text.splitlines()) + 12)
    if title in {"Icons", "Original icons", "New SVG icons"}:
        width = max(width, 72)
    button.setFixedSize(width, _AI_BUTTON_H)
    button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    button.setStyleSheet(
        "QToolButton {"
        f"min-width: {width}px; max-width: {width}px;"
        f"min-height: {_AI_BUTTON_H}px; max-height: {_AI_BUTTON_H}px;"
        "padding: 0; margin: 0; border: none; border-radius: 4px;"
        "background: transparent; color: palette(button-text); }"
        "QToolButton:hover { background: palette(midlight); }"
        "QToolButton:pressed { background: palette(mid); }"
        "QToolButton:checked { background: palette(midlight); }"
        "QToolButton:focus { border: 1px solid palette(highlight); }"
        "QToolButton::menu-indicator { image: none; width: 0; }"
    )
    button.setProperty("codexToolbarStyled", _AI_TOOLBAR_STYLE_VERSION)
    button.updateGeometry()


def _style_ai_section_label(label):
    try:
        from Qt.QtCore import Qt
    except Exception:
        return
    try:
        label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        label.setMinimumHeight(18)
        font = label.font()
        font.setPixelSize(11)
        try:
            font.setWeight(600)
        except Exception:
            from Qt.QtGui import QFont

            font.setWeight(QFont.Weight.DemiBold)
        label.setFont(font)
    except Exception:
        pass


def _style_ai_section_widget(widget):
    """Leave a little space above the artwork and below the section title."""
    layout = widget.layout()
    if layout is None:
        return
    margins = layout.contentsMargins()
    layout.setContentsMargins(
        margins.left(), _AI_SECTION_TOP_PX,
        margins.right(), _AI_SECTION_BOTTOM_PX,
    )
    layout.invalidate()
    widget.updateGeometry()


def _patch_tabbedtoolbar_section():
    """Style buttons re-created for the toolbar's overflow popup.

    ChimeraX's _Section is a QWidgetAction; when the toolbar runs out of
    horizontal space, Qt opens an extension popup and calls createWidget()
    again with the popup as parent, producing a brand-new QWidget full of
    fresh QToolButtons that never see style_ai_toolbar(). Wrapping
    createWidget lets us apply the same styling to those popup-side buttons.
    """
    try:
        from chimerax.ui.widgets import tabbedtoolbar
    except Exception:
        return
    section_cls = getattr(tabbedtoolbar, "_Section", None)
    if section_cls is None:
        return
    if getattr(section_cls, "_codex_bridge_create_widget_patch_version", 0) == _AI_TOOLBAR_STYLE_VERSION:
        return
    original_create_widget = getattr(
        section_cls,
        "_codex_bridge_original_create_widget",
        section_cls.createWidget,
    )

    def patched_create_widget(self, parent):
        widget = original_create_widget(self, parent)
        try:
            section_title = getattr(self, "section_title", None)
            if section_title in _AI_TOOLBAR_SECTION_TITLES:
                from Qt.QtWidgets import QLabel, QToolButton

                for button in widget.findChildren(QToolButton):
                    _style_ai_button_widget(button)
                _style_ai_section_widget(widget)
                for label in widget.findChildren(QLabel):
                    try:
                        label_text = str(label.text() or "").strip()
                    except Exception:
                        continue
                    if label_text in _AI_TOOLBAR_SECTION_TITLES:
                        _style_ai_section_label(label)
        except Exception:
            pass
        from .icon_theme import style_section_theme
        style_section_theme(self, widget)
        return widget

    section_cls.createWidget = patched_create_widget
    section_cls._codex_bridge_original_create_widget = original_create_widget
    section_cls._codex_bridge_create_widget_patched = True
    section_cls._codex_bridge_create_widget_patch_version = _AI_TOOLBAR_STYLE_VERSION


def style_ai_toolbar(session):
    """Normalize Codex Bridge toolbar button geometry after ChimeraX rebuilds it."""
    try:
        from Qt.QtCore import QSize, Qt
        from Qt.QtWidgets import QLabel, QSizePolicy, QToolButton
        from chimerax.toolbar.tool import get_toolbar_singleton
    except Exception:
        return

    try:
        toolbar_tool = get_toolbar_singleton(session, create=False)
        ttb = getattr(toolbar_tool, "ttb", None)
    except Exception:
        return
    if ttb is None:
        return

    from .icon_theme import apply_icon_theme
    apply_icon_theme(session, ttb)

    for button in ttb.findChildren(QToolButton):
        _style_ai_button_widget(button)
    for label in ttb.findChildren(QLabel):
        if str(label.text() or "").strip() in _AI_TOOLBAR_SECTION_TITLES:
            _style_ai_section_label(label)
    for tab, sections in getattr(ttb, "_buttons", {}).items():
        if tab not in {"AI", "Molecule Display", "Nucleotides"}:
            continue
        for title, section in sections.items():
            if title not in _AI_TOOLBAR_SECTION_TITLES or not hasattr(section, "createdWidgets"):
                continue
            for widget in section.createdWidgets():
                _style_ai_section_widget(widget)

    try:
        from Qt.QtWidgets import QWidget
    except Exception:
        QWidget = None

    if QWidget is not None:
        try:
            existing = None
            for button in ttb.findChildren(QToolButton):
                if str(button.text() or "").strip() == "RAPiDock":
                    existing = button
                    break
            if existing is None:
                section_parent = None
                for label in ttb.findChildren(QLabel):
                    if str(label.text() or "").strip() == "Modeling":
                        section_parent = label.parentWidget()
                        break
                if section_parent is not None:
                    from Qt.QtWidgets import QToolButton as _QToolButton
                    from pathlib import Path
                    from Qt.QtGui import QIcon

                    rapidock_button = _QToolButton(section_parent)
                    rapidock_button.setText("RAPiDock")
                    rapidock_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
                    rapidock_button.setIcon(QIcon(str(Path(__file__).resolve().with_name("icons") / "rapidock-logo.svg")))
                    rapidock_button.setIconSize(QSize(_AI_ICON_PX, _AI_ICON_PX))
                    rapidock_button.setFixedSize(_AI_BUTTON_W, _AI_BUTTON_H)
                    fixed_policy = _fixed_size_policy(QSizePolicy)
                    rapidock_button.setSizePolicy(fixed_policy, fixed_policy)
                    rapidock_button.setMinimumWidth(_AI_BUTTON_W)
                    rapidock_button.setMaximumWidth(_AI_BUTTON_W)
                    rapidock_button.setMinimumHeight(_AI_BUTTON_H)
                    rapidock_button.setMaximumHeight(_AI_BUTTON_H)
                    _style_ai_button_widget(rapidock_button)
                    try:
                        from .toolbar_actions import _prompt_peptide_sequence, launch_rapidock_prediction

                        def _run_rapidock_from_toolbar(_checked=False, ses=session):
                            peptide = _prompt_peptide_sequence(ses)
                            if not peptide:
                                return
                            try:
                                launch_rapidock_prediction(ses, peptide=peptide)
                            except Exception as err:
                                try:
                                    ses.logger.error(str(err) if str(err) else err.__class__.__name__)
                                except Exception:
                                    pass

                        rapidock_button.clicked.connect(_run_rapidock_from_toolbar)
                    except Exception:
                        pass
                    section_layout = section_parent.layout()
                    if section_layout is not None:
                        section_layout.addWidget(rapidock_button)
                    rapidock_button.show()
                    rapidock_button.raise_()
        except Exception:
            pass


def _patch_builtin_log_css():
    try:
        from chimerax.log import tool as log_tool
    except Exception:
        return

    if getattr(log_tool, "_codex_bridge_log_font_patch_version", None) == _LOG_FONT_PATCH_VERSION:
        return

    original_cxcmd_css = getattr(
        log_tool,
        "_codex_bridge_original_cxcmd_css",
        getattr(log_tool, "cxcmd_css", None),
    )
    if original_cxcmd_css is None:
        return

    def cxcmd_css_with_monospace(exec_links, _original=original_cxcmd_css):
        css = _original(exec_links)
        if "codex-bridge-log-font" not in css:
            css += "\n" + _LOG_MONO_CSS
        return css

    log_tool._codex_bridge_original_cxcmd_css = original_cxcmd_css
    log_tool.cxcmd_css = cxcmd_css_with_monospace
    log_tool._codex_bridge_log_font_patched = True
    log_tool._codex_bridge_log_font_patch_version = _LOG_FONT_PATCH_VERSION


def _refresh_open_builtin_log(session):
    try:
        tools = list(session.tools.list())
    except Exception:
        return
    for tool in tools:
        try:
            tool_name = getattr(tool, "tool_name", "")
        except Exception:
            continue
        if tool_name != "Log":
            continue
        try:
            tool.show_page_source()
        except Exception:
            try:
                tool._show()
            except Exception:
                pass


def _patch_blastprotein_pdbinfo():
    try:
        from chimerax.blastprotein.data_model import pdbinfo
    except Exception:
        return

    if getattr(pdbinfo, "_codex_bridge_patched", False):
        return

    original = getattr(pdbinfo, "fetch_pdb_info", None)
    if original is None:
        return

    def fetch_pdb_info_json(entry_chain_list):
        query = pdbinfo.query_template % ",".join(
            ['"%s"' % entry_chain.split("_")[0] for entry_chain in entry_chain_list]
        )
        payload = json.dumps({"query": query}).encode("utf-8")
        req = Request(
            "https://data.rcsb.org/graphql",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with urlopen(req, timeout=30) as f:
            info = json.loads(f.read().decode("utf-8"))
        if "errors" in info:
            raise ValueError("Fetching BLAST PDB info had errors: %s" % info["errors"])

        by_entry = {}
        for entry_data in info["data"]["entries"]:
            by_entry[entry_data["rcsb_id"]] = entry_data

        pdb_info = {}
        for info_key in entry_chain_list:
            entry, chain = info_key.split("_")
            pdb_info[info_key] = hits = {}
            if entry not in by_entry:
                continue
            for attr_name, mmcif_keys in pdbinfo.entry_attr_name_mapping:
                hits[attr_name] = pdbinfo.get_val(by_entry[entry], mmcif_keys)
            all_polys = by_entry[entry]["polymer_entities"]
            for poly in all_polys:
                try:
                    ids = poly["rcsb_polymer_entity_container_identifiers"]
                except KeyError:
                    continue
                if ids is None:
                    continue
                try:
                    auth_ids = ids["auth_asym_ids"]
                except KeyError:
                    continue
                if auth_ids and chain in auth_ids:
                    break
            else:
                poly = None
            for attr_name, per_chain, mmcif_keys in pdbinfo.chain_attr_name_mapping:
                if per_chain:
                    val = None if poly is None else pdbinfo.get_val(poly, mmcif_keys)
                else:
                    if all_polys is None:
                        val = None
                    elif len(mmcif_keys) == 1 and callable(mmcif_keys[0]):
                        val = mmcif_keys[0](all_polys)
                    else:
                        val = []
                        for p in all_polys:
                            pval = pdbinfo.get_val(p, mmcif_keys)
                            auth_ids = pdbinfo.get_val(
                                p,
                                [
                                    "rcsb_polymer_entity_container_identifiers",
                                    "auth_asym_ids",
                                ],
                            )
                            if auth_ids and chain in auth_ids:
                                if isinstance(pval, list):
                                    val.extend(pval)
                                elif pval is not None:
                                    val.append(pval)
                        if len(val) == 0:
                            val = None
                hits[attr_name] = val
        return pdb_info

    pdbinfo.fetch_pdb_info = fetch_pdb_info_json
    pdbinfo._codex_bridge_patched = True
