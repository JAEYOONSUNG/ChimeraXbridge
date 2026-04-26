import json
from urllib.request import Request, urlopen


_LOG_FONT_PATCH_VERSION = 1
_AI_TOOLBAR_STYLE_VERSION = 1

_AI_TOOLBAR_BUTTON_TITLES = {
    "Analyze",
    "View",
    "Site",
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
    "Boltz",
    "HHpred",
    "Catalytic",
    "Membrane",
    "PISA",
    "CAVER",
    "DALI",
    "VAST",
    "PDBeFold",
    "US-align",
    "Display Ctrl",
}

_AI_TOOLBAR_SECTION_TITLES = {"Quick", "Sequence", "Modeling", "Sites", "Structure", "AI Tools", "Adjust"}

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
    if getattr(session, "_codex_bridge_runtime_patches_applied", False):
        return
    session._codex_bridge_runtime_patches_applied = True
    _patch_blastprotein_pdbinfo()


def style_builtin_log(session=None):
    _patch_builtin_log_css()
    if session is not None:
        _refresh_open_builtin_log(session)


def style_ai_toolbar(session):
    """Normalize Codex Bridge toolbar button geometry after ChimeraX rebuilds it."""
    try:
        from Qt.QtCore import QSize, Qt
        from Qt.QtWidgets import QLabel, QToolButton
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
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setIconSize(QSize(34, 34))
            button.setMinimumHeight(68)
            button.setMaximumHeight(76)
            button.setMinimumWidth(max(button.minimumWidth(), 54))
            font = button.font()
            font.setPointSize(10)
            try:
                font.setWeight(500)
            except Exception:
                from Qt.QtGui import QFont

                font.setWeight(QFont.Weight.Medium)
            button.setFont(font)
            button.setStyleSheet(
                "QToolButton {"
                " padding: 1px 5px 3px 5px;"
                " margin: 0px;"
                " text-align: center;"
                "}"
                "QToolButton::menu-indicator { image: none; width: 0px; }"
            )
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
        with urlopen(req) as f:
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
