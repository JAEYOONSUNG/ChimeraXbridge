import json
from urllib.request import Request, urlopen


_LOG_FONT_PATCH_VERSION = 1
_AI_TOOLBAR_STYLE_VERSION = 20
_MODEL_PANEL_ID_REORDER_PATCH_VERSION = 2
# Uniform geometry across the entire AI tab. Every button is forced to the
# same icon size, button width, AND button height so:
#   - the inter-icon horizontal rhythm is identical in every section, and
#   - the label baseline lands at the same Y in every button (QToolButton
#     centres the icon+text block vertically inside the button, so equal
#     icon size + equal button height = labels on the same baseline).
#
# Tightened from 34/56 to 30/48 because Quick-section SVGs carry generous
# internal margins (visible icon is ~24px of a 30px box), which made the
# inter-icon gap feel wider than in Sequence/Modeling where the icons fill
# more of their box. 9pt font keeps the longest labels (AlphaFold, PyRosetta,
# HPEPDOCK) inside 48px without clipping.
_AI_ICON_PX = 30
_AI_BUTTON_W = 46
_AI_BUTTON_H = 64
_AI_FONT_PT = 9

_AI_TOOLBAR_BUTTON_TITLES = {
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
    try:
        from .auto_reload import install_auto_reload_watcher

        install_auto_reload_watcher(session)
    except Exception:
        pass
    if getattr(session, "_codex_bridge_runtime_patches_applied", False):
        return
    session._codex_bridge_runtime_patches_applied = True
    _patch_blastprotein_pdbinfo()


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
    """Force uniform icon size + button width on an AI-tab QToolButton.

    Two reasons we override:
      - Modeling icons are a mix of PNG (small native) and SVG (vector). Qt
        renders PNGs at their native size unless we set iconSize explicitly,
        so the section looks ragged. _AI_ICON_PX makes every icon land at the
        same pixel size.
      - Long labels ("AF Complex", "AlphaFold", "HPEPDOCK") naturally produce
        wide buttons while short labels ("Boltz", "MLP") produce narrow ones.
        Adjacent buttons end up with visibly different inter-icon gaps. A
        uniform minimum width fixes the spacing rhythm; the maximum lets
        especially-long labels grow if they truly need it.
    """
    try:
        from Qt.QtCore import QSize, Qt
        from Qt.QtWidgets import QSizePolicy
    except Exception:
        return
    try:
        title = " ".join(str(button.text() or "").split())
    except Exception:
        return
    if title not in _AI_TOOLBAR_BUTTON_TITLES:
        return
    try:
        display_text = _AI_TOOLBAR_DISPLAY_TEXT.get(title, title)
        if str(button.text() or "") != display_text:
            button.setText(display_text)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.setIconSize(QSize(_AI_ICON_PX, _AI_ICON_PX))
        button.setFixedSize(_AI_BUTTON_W, _AI_BUTTON_H)
        fixed_policy = _fixed_size_policy(QSizePolicy)
        button.setSizePolicy(fixed_policy, fixed_policy)
        button.setMinimumWidth(_AI_BUTTON_W)
        button.setMaximumWidth(_AI_BUTTON_W)
        button.setMinimumHeight(_AI_BUTTON_H)
        button.setMaximumHeight(_AI_BUTTON_H)
        font = button.font()
        font.setPointSize(_AI_FONT_PT)
        try:
            font.setWeight(500)
        except Exception:
            from Qt.QtGui import QFont

            font.setWeight(QFont.Weight.Medium)
        button.setFont(font)
        # The QSS width clamps are a belt-and-braces backup to setMinimumWidth
        # / setMaximumWidth: some Qt styles ignore the C++ size hints when
        # computing column widths inside a QGridLayout, but they always honour
        # the QSS min-width / max-width.
        button.setStyleSheet(
            "QToolButton {"
            f" min-width: {_AI_BUTTON_W}px;"
            f" max-width: {_AI_BUTTON_W}px;"
            f" min-height: {_AI_BUTTON_H}px;"
            f" max-height: {_AI_BUTTON_H}px;"
            " padding: 0px;"
            " margin: 0px;"
            " text-align: center;"
            "}"
            "QToolButton::menu-indicator { image: none; width: 0px; }"
        )
        try:
            button.updateGeometry()
            parent = button.parentWidget()
            if parent is not None and parent.layout() is not None:
                parent.layout().invalidate()
        except Exception:
            pass
        button.setProperty("codexToolbarStyled", _AI_TOOLBAR_STYLE_VERSION)
    except Exception:
        pass


def _style_ai_section_label(label):
    try:
        from Qt.QtCore import Qt
    except Exception:
        return
    try:
        label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        label.setMinimumHeight(18)
        font = label.font()
        font.setPointSize(11)
        try:
            font.setWeight(600)
        except Exception:
            from Qt.QtGui import QFont

            font.setWeight(QFont.Weight.DemiBold)
        label.setFont(font)
    except Exception:
        pass


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
                for label in widget.findChildren(QLabel):
                    try:
                        label_text = str(label.text() or "").strip()
                    except Exception:
                        continue
                    if label_text in _AI_TOOLBAR_SECTION_TITLES:
                        _style_ai_section_label(label)
        except Exception:
            pass
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

    for button in ttb.findChildren(QToolButton):
        try:
            title = " ".join(str(button.text() or "").split())
        except Exception:
            continue
        if title not in _AI_TOOLBAR_BUTTON_TITLES:
            continue
        try:
            display_text = _AI_TOOLBAR_DISPLAY_TEXT.get(title, title)
            if str(button.text() or "") != display_text:
                button.setText(display_text)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setIconSize(QSize(_AI_ICON_PX, _AI_ICON_PX))
            # Uniform width + height so every AI-tab button is geometrically
            # identical: same inter-icon horizontal gap across every section,
            # same label baseline across every button. Multi-word labels
            # ("AF Complex", "Action Pad") still wrap onto two lines via
            # tabbedtoolbar.split_title() because we set a sufficient height.
            button.setFixedSize(_AI_BUTTON_W, _AI_BUTTON_H)
            fixed_policy = _fixed_size_policy(QSizePolicy)
            button.setSizePolicy(fixed_policy, fixed_policy)
            button.setMinimumWidth(_AI_BUTTON_W)
            button.setMaximumWidth(_AI_BUTTON_W)
            button.setMinimumHeight(_AI_BUTTON_H)
            button.setMaximumHeight(_AI_BUTTON_H)
            font = button.font()
            font.setPointSize(_AI_FONT_PT)
            try:
                font.setWeight(500)
            except Exception:
                from Qt.QtGui import QFont

                font.setWeight(QFont.Weight.Medium)
            button.setFont(font)
            # QSS clamps mirror the C++ size hints. Some Qt styles compute
            # column widths from the QSS first and ignore the C++ min/max
            # entirely -- this redundancy keeps the AI tab uniform regardless
            # of the active QStyle.
            button.setStyleSheet(
                "QToolButton {"
                f" min-width: {_AI_BUTTON_W}px;"
                f" max-width: {_AI_BUTTON_W}px;"
                f" min-height: {_AI_BUTTON_H}px;"
                f" max-height: {_AI_BUTTON_H}px;"
                " padding: 0px;"
                " margin: 0px;"
                " text-align: center;"
                "}"
                "QToolButton::menu-indicator { image: none; width: 0px; }"
            )
            try:
                button.updateGeometry()
                parent = button.parentWidget()
                if parent is not None and parent.layout() is not None:
                    parent.layout().invalidate()
            except Exception:
                pass
            button.setProperty("codexToolbarStyled", _AI_TOOLBAR_STYLE_VERSION)
        except Exception:
            pass

    for label in ttb.findChildren(QLabel):
        try:
            title = str(label.text() or "").strip()
        except Exception:
            continue
        if title not in _AI_TOOLBAR_SECTION_TITLES:
            continue
        try:
            label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            label.setMinimumHeight(18)
            font = label.font()
            font.setPointSize(11)
            try:
                font.setWeight(600)
            except Exception:
                from Qt.QtGui import QFont

                font.setWeight(QFont.Weight.DemiBold)
            label.setFont(font)
        except Exception:
            pass

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
                    rapidock_button.setProperty("codexToolbarStyled", _AI_TOOLBAR_STYLE_VERSION)
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
