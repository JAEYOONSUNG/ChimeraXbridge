from chimerax.core.toolshed import BundleAPI
import os as _os

# macOS GUI launchd starts apps with a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin)
# which doesn't include /usr/local/bin or /opt/homebrew/bin. That breaks every
# subprocess.run(["docker", ...]) / ["mafft", ...] / ["rsvg-convert", ...] call
# inside this bundle. Augment PATH here, before any tool starts.
_PATH_EXTRA = ("/usr/local/bin", "/opt/homebrew/bin", "/usr/local/sbin")
_existing = _os.environ.get("PATH", "")
_existing_parts = _existing.split(":") if _existing else []
for _p in _PATH_EXTRA:
    if _p not in _existing_parts:
        _existing_parts.insert(0, _p)
_os.environ["PATH"] = ":".join(_existing_parts)

# Pick up RAPiDock setup variables when the user has them in ~/.zshrc but the
# bundle was launched from Finder/Dock without those exports. We read .zshrc
# once and only set what isn't already in os.environ.
def _hydrate_rapidock_env():
    rc = _os.path.expanduser("~/.zshrc")
    if not _os.path.exists(rc):
        return
    try:
        with open(rc) as fh:
            for line in fh:
                line = line.strip()
                if not line.startswith("export "):
                    continue
                pair = line[len("export "):].lstrip()
                if "=" not in pair:
                    continue
                key, _, val = pair.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                # Only hydrate the codex_bridge / RAPiDock relevant vars.
                if not key.startswith(("RAPIDOCK_", "CHIMERAX_CODEX_")):
                    continue
                if key in _os.environ:
                    continue
                # Expand $HOME-style references.
                _os.environ[key] = _os.path.expandvars(val)
    except Exception:
        pass

_hydrate_rapidock_env()


def _dock_title(dock_widget):
    try:
        return str(dock_widget.windowTitle() or dock_widget.objectName() or "")
    except Exception:
        return ""


def _find_dock_widget(session, title_tokens):
    main_window = getattr(session.ui, "main_window", None)
    if main_window is None:
        return None
    try:
        from Qt.QtWidgets import QDockWidget
    except Exception:
        return None

    tokens = tuple(str(token).lower() for token in title_tokens)
    for dock_widget in main_window.findChildren(QDockWidget):
        title = _dock_title(dock_widget).lower()
        if any(token in title for token in tokens):
            return dock_widget
    return None


def _right_side_docks(session):
    main_window = getattr(session.ui, "main_window", None)
    if main_window is None:
        return []
    try:
        from Qt.QtCore import Qt
        from Qt.QtWidgets import QDockWidget
    except Exception:
        return []

    docks = []
    for dock_widget in main_window.findChildren(QDockWidget):
        try:
            if dock_widget.isFloating():
                continue
            if main_window.dockWidgetArea(dock_widget) != Qt.DockWidgetArea.RightDockWidgetArea:
                continue
        except Exception:
            continue
        docks.append(dock_widget)
    return docks


def _relax_dock_content_constraints(dock_widgets):
    try:
        from Qt.QtCore import QSize
        from Qt.QtWidgets import QAbstractButton, QAbstractScrollArea, QComboBox, QLayout, QSizePolicy, QWidget
    except Exception:
        return

    compact_tokens = ("models", "model panel", "action pad", "ai assistant", "log")
    aggressive_tokens = ("models", "model panel", "action pad")
    height_locked_tokens = ("ai assistant",)

    for dock_widget in tuple(dict.fromkeys(dock_widgets)):
        title = _dock_title(dock_widget).lower()
        if not any(token in title for token in compact_tokens):
            continue
        aggressive = any(token in title for token in aggressive_tokens)
        height_locked = any(token in title for token in height_locked_tokens)
        root = dock_widget.widget()
        widgets = [dock_widget]
        if root is not None:
            widgets.append(root)
            try:
                widgets.extend(root.findChildren(QWidget))
            except Exception:
                pass

        for widget in tuple(dict.fromkeys(widgets)):
            protected_control = isinstance(widget, (QAbstractButton, QComboBox))
            try:
                widget.setMaximumWidth(16777215)
                if not protected_control:
                    widget.setMinimumWidth(0)
                if protected_control:
                    # Keep explicit button/combo heights; only prevent collapse.
                    widget.setMinimumHeight(24)
                else:
                    widget.setMinimumSize(0, 0)
                    widget.setMinimumHeight(0)
                    if not height_locked:
                        widget.setMaximumHeight(16777215)
            except Exception:
                pass

            if aggressive and not protected_control:
                try:
                    policy = widget.sizePolicy()
                    policy.setVerticalPolicy(QSizePolicy.Policy.Ignored)
                    widget.setSizePolicy(policy)
                except Exception:
                    pass

            try:
                layout = widget.layout()
                if layout is not None:
                    layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
            except Exception:
                pass

            if isinstance(widget, QAbstractScrollArea):
                try:
                    widget.setMinimumViewportSize(QSize(0, 0))
                    widget.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
                except Exception:
                    pass
                try:
                    widget.viewport().setMinimumSize(0, 0)
                    widget.viewport().setMinimumHeight(0)
                except Exception:
                    pass


def _release_dock_constraints(dock_widgets):
    for dock_widget in tuple(dict.fromkeys(dock_widgets)):
        try:
            dock_widget.setMinimumWidth(0)
            dock_widget.setMaximumWidth(16777215)
            dock_widget.setMinimumHeight(0)
            dock_widget.setMaximumHeight(16777215)
        except Exception:
            pass
    _relax_dock_content_constraints(dock_widgets)


def _ensure_action_pad_models_tab(session, raise_action=False):
    if not getattr(session.ui, "is_gui", False):
        return None
    main_window = getattr(session.ui, "main_window", None)
    if main_window is None:
        return None
    try:
        from .action_pad import CodexActionPad

        action_pad = CodexActionPad.get_singleton(session, create=True, display=True)
    except Exception:
        return None

    _schedule_helper_dock_layout(
        session,
        raise_tool="action pad" if raise_action else "models",
    )
    return action_pad


def _ensure_display_controls_tab(session, raise_controls=False):
    if not getattr(session.ui, "is_gui", False):
        return None
    try:
        from .display_controls import CodexDisplayControls

        display_controls = CodexDisplayControls.get_singleton(session, create=True, display=True)
        if display_controls is not None:
            try:
                display_controls.display(True)
            except Exception:
                pass
    except Exception:
        return None

    _schedule_helper_dock_layout(
        session,
        raise_tool="display controls" if raise_controls else "models",
    )
    return display_controls


# Title tokens (lowercased substring) for each bottom helper tool dock. Log is
# intentionally excluded: the right dock layout is Log on top, with all helper
# tools tabified below it.
_HELPER_DOCK_TOKENS = (
    ("models",            ("models", "model panel")),
    ("ai assistant",      ("ai assistant",)),
    ("display controls",  ("display controls", "display ctrl")),
    ("action pad",        ("action pad",)),
    ("camera bookmarks",  ("camera bookmarks", "bookmarks")),
)


def _schedule_helper_dock_layout(session, *, raise_tool=None):
    """Retry right-side tab layout after dock widgets finish constructing."""
    try:
        _tabify_helper_tools(session, raise_tool=raise_tool)
    except Exception:
        pass
    try:
        from Qt.QtCore import QTimer
    except Exception:
        return
    for delay in (80, 250, 700, 1500):
        try:
            QTimer.singleShot(
                delay,
                lambda ses=session, tool=raise_tool: _tabify_helper_tools(ses, raise_tool=tool),
            )
        except Exception:
            pass


def _tabify_helper_tools(session, *, raise_tool=None):
    """Tabify helper tools below Log in the right dock.

    The desired layout is:

      right dock, upper row: ChimeraX Log
      right dock, lower row: Models / AI Assistant / Display Ctrl /
                            Action Pad / Bookmarks as tabs

    ``raise_tool`` is a substring of a dock title; the matching dock is
    raised to the front after tabifying so the caller's tool comes up
    visible.
    """
    if not getattr(session.ui, "is_gui", False):
        return
    main_window = getattr(session.ui, "main_window", None)
    if main_window is None:
        return
    try:
        from Qt.QtCore import Qt
    except Exception:
        return

    found = []
    seen_ids = set()
    for key, tokens in _HELPER_DOCK_TOKENS:
        dock = _find_dock_widget(session, tokens)
        if dock is not None and id(dock) not in seen_ids:
            seen_ids.add(id(dock))
            found.append((key, dock))
    if not found:
        return

    anchor = found[0][1]
    right_area = Qt.DockWidgetArea.RightDockWidgetArea

    def _dock_to_right(dock):
        if dock is None:
            return
        try:
            if dock.isFloating():
                dock.setFloating(False)
        except Exception:
            pass
        try:
            if main_window.dockWidgetArea(dock) != right_area:
                main_window.addDockWidget(right_area, dock)
        except Exception:
            pass

    log_dock = _find_dock_widget(session, ("log",))
    if log_dock is not None:
        _dock_to_right(log_dock)
    for _key, dock in found:
        _dock_to_right(dock)

    if log_dock is not None and log_dock is not anchor:
        try:
            main_window.splitDockWidget(log_dock, anchor, Qt.Orientation.Vertical)
        except Exception:
            pass

    for _key, dock in found:
        if dock is anchor:
            continue
        try:
            main_window.tabifyDockWidget(anchor, dock)
        except Exception:
            pass

    raise_dock = None
    if raise_tool:
        needle = raise_tool.strip().lower()
        for key, dock in found:
            if needle in key:
                raise_dock = dock
                break
    if raise_dock is None:
        raise_dock = anchor
    try:
        raise_dock.raise_()
    except Exception:
        pass

    right_docks = _right_side_docks(session)
    if right_docks:
        target_width = max(380, int(max(main_window.width(), 900) * 0.30))
        try:
            main_window.resizeDocks(
                right_docks,
                [target_width] * len(right_docks),
                Qt.Orientation.Horizontal,
            )
        except Exception:
            pass
        _release_dock_constraints(right_docks)

    if log_dock is not None and log_dock is not anchor:
        available_height = max(main_window.height(), 800)
        log_height = max(150, int(available_height * 0.24))
        helper_height = max(380, int(available_height * 0.76))
        try:
            main_window.resizeDocks(
                [log_dock, anchor],
                [log_height, helper_height],
                Qt.Orientation.Vertical,
            )
        except Exception:
            pass
        _release_dock_constraints([log_dock, anchor])


def _apply_startup_layout(session, assistant=None):
    if not getattr(session.ui, "is_gui", False):
        return
    main_window = getattr(session.ui, "main_window", None)
    if main_window is None:
        return

    try:
        from .runtime_patches import style_builtin_log

        style_builtin_log(session)
    except Exception:
        pass

    try:
        from Qt.QtCore import Qt
    except Exception:
        return

    session._codex_bridge_dock_fraction = 0.30

    if assistant is not None:
        try:
            assistant._apply_dock_fraction()
        except Exception:
            pass

    _ensure_action_pad_models_tab(session)
    _ensure_display_controls_tab(session)
    _tabify_helper_tools(session, raise_tool="ai assistant" if assistant is not None else "models")

    assistant_dock = _find_dock_widget(session, ("ai assistant",))
    if assistant_dock is not None:
        _release_dock_constraints([assistant_dock])


def _install_runtime_toolbar_buttons(session, force_rebuild=False):
    if not getattr(session.ui, "is_gui", False):
        return
    toolbar_version = 55
    toolbar = getattr(session, "toolbar", None)
    if toolbar is None:
        return
    try:
        bundle_info = session.toolshed.find_bundle("ChimeraX-CodexBridge", session.logger, installed=True)
    except Exception:
        bundle_info = None
    if bundle_info is None:
        return

    def provider(name, tab, section=None, **kwargs):
        data = {"tab": tab}
        if section is not None:
            data["section"] = section
        data.update(kwargs)
        return name, data

    providers = [
        provider("ai-tab", "AI", after="Right Mouse", help="help:user/tools/codex_assistant.html"),
        provider("ai-layout-quick", "AI", "Quick"),
        provider("ai-layout-sequence", "AI", "Sequence", after="Quick"),
        provider("ai-layout-modeling", "AI", "Modeling", after="Sequence"),
        provider("ai-layout-sites", "AI", "Sites", after="Modeling"),
        provider("ai-layout-channels", "AI", "Channels", after="Sites"),
        provider("ai-layout-structure", "AI", "Structure", after="Channels"),
        provider(
            "ai-quick-analyze",
            "AI",
            "Quick",
            display_name="Analyze",
            icon="ai-analyze.svg",
            description="Run AI analysis on the current selection",
        ),
        provider(
            "ai-quick-view",
            "AI",
            "Quick",
            display_name="View",
            icon="ai-view.svg",
            description="Apply the next AI-guided structure view",
        ),
        provider(
            "ai-quick-site",
            "AI",
            "Quick",
            display_name="Pocket",
            icon="ai-site.svg",
            description="Find and focus the most likely ligand-binding pocket",
        ),
        provider(
            "ai-quick-cavity",
            "AI",
            "Quick",
            display_name="Cavity",
            icon="ai-cavity.svg",
            description="Detect substrate-binding cavity and overlay a translucent surface",
        ),
        provider(
            "ai-quick-figure",
            "AI",
            "Quick",
            display_name="Figure",
            icon="ai-figure.svg",
            description="Cycle figure-ready views",
        ),
        provider(
            "ai-quick-triad-zoom",
            "AI",
            "Quick",
            display_name="Zoom",
            icon="ai-zoom.svg",
            description="Fade scaffold cartoon to 80% transparency and zoom into the catalytic triad close-up",
        ),
        provider(
            "ai-analysis-blast",
            "AI",
            "Sequence",
            display_name="Blast",
            icon="blast-logo.png",
            description="Run ChimeraX native Blast Protein for the current chain",
        ),
        provider(
            "ai-analysis-profile",
            "AI",
            "Sequence",
            display_name="Profile",
            icon="uniprot-logo.png",
            description="Open UniProt BLAST with the current protein sequence",
        ),
        provider(
            "ai-analysis-hhpred",
            "AI",
            "Sequence",
            display_name="HHpred",
            icon="hhpred-logo.svg",
            description="Open HHpred / HHblits with the current protein sequence",
        ),
        provider(
            "ai-analysis-signalp",
            "AI",
            "Sequence",
            display_name="SignalP",
            icon="ai-signalp.png",
            description="Predict signal peptides, cleavage sites, and prodomain-like N-terminal regions",
        ),
        provider(
            "ai-analysis-conserve",
            "AI",
            "Sequence",
            display_name="Consurf",
            icon="consurf-logo.png",
            description="Run local ConSurf-lite conservation view for the current structure",
        ),
        provider(
            "ai-analysis-3dconserve",
            "AI",
            "Sequence",
            display_name="Conserve",
            icon="ai-conserve.png",
            description="Highlight sequence-unique residues across the structures currently open and aligned",
        ),
        provider(
            "ai-analysis-hydrophobicity",
            "AI",
            "Sequence",
            display_name="MLP",
            icon="ai-hydrophobicity.svg",
            description="Color all visible structures by hydrophobicity (mlp) on a fixed scale so they are directly comparable",
        ),
        provider(
            "ai-analysis-alphafold",
            "AI",
            "Modeling",
            display_name="AlphaFold",
            icon="alphafold-logo.png",
            description="Run ChimeraX native AlphaFold match/search for the current chain",
        ),
        provider(
            "ai-analysis-afcomplex",
            "AI",
            "Modeling",
            display_name="AF Complex",
            icon="ai-alphafold.svg",
            description="Paste DNA/RNA sequence and open AlphaFold Server with current protein chains",
        ),
        provider(
            "ai-analysis-nucdock",
            "AI",
            "Modeling",
            display_name="NucDock",
            icon="hdock-logo.png",
            description="Paste DNA/RNA sequence and dock it against the current receptor with HDOCK",
        ),
        provider(
            "ai-analysis-boltz",
            "AI",
            "Modeling",
            display_name="Boltz",
            icon="boltz-logo.svg",
            description="Run official latest Boltz CLI on current chains, or open native Boltz panel",
        ),
        provider(
            "ai-analysis-rapidock",
            "AI",
            "Modeling",
            display_name="RAPiDock",
            icon="rapidock-logo.svg",
            description="Dock a peptide against the current protein with RAPiDock (engine picker: native/Docker/HPEPDOCK)",
        ),
        provider(
            "ai-analysis-hpepdock",
            "AI",
            "Modeling",
            display_name="HPEPDOCK",
            icon="hpepdock-logo.svg",
            description="Submit peptide + receptor to HPEPDOCK 2.0 web service (works on macOS, no GPU)",
        ),
        provider(
            "ai-analysis-md",
            "AI",
            "Modeling",
            display_name="MD",
            icon="ai-md.svg",
            description="Run OpenMM MD on the chosen model in a solvated, neutralised, periodic box (PME, NPT, AMBER ff14SB + TIP3P) -- complex stays inside the simulation cell",
        ),
        provider(
            "ai-setup-pyrosetta",
            "AI",
            "Modeling",
            display_name="PyRosetta",
            icon="ai-pyrosetta.svg",
            description="One-click install of PyRosetta into the RAPiDock Docker image, enabling ref2015 scoring + relaxed poses",
        ),
        provider(
            "ai-analysis-energy",
            "AI",
            "Sites",
            display_name="Energy",
            icon="ai-energy.svg",
            description="Score the current structure with PyRosetta ref2015 + InterfaceAnalyzer (requires PyRosetta button first)",
        ),
        provider(
            "ai-analysis-catalytic",
            "AI",
            "Sites",
            display_name="Catalytic",
            icon="ai-catalytic.svg",
            description="Rank and highlight catalytic residue candidates",
        ),
        provider(
            "ai-analysis-interface",
            "AI",
            "Sites",
            display_name="Interface",
            icon="ai-interface.svg",
            description="Pick enzyme + ligand, then visualize the binding interface with pastel H-bond / salt-bridge / pi-stacking / hydrophobic pseudobonds",
        ),
        provider(
            "ai-analysis-folddisco",
            "AI",
            "Sites",
            display_name="FoldDisco",
            icon="folddisco-logo.png",
            description="Search a selected structural motif with FoldDisco",
        ),
        provider(
            "ai-analysis-caver",
            "AI",
            "Channels",
            display_name="CAVER",
            icon="caverweb-logo.svg",
            description="Export current/selected structure and open CAVER Web tunnel/channel analysis",
        ),
        provider(
            "ai-analysis-membrane",
            "AI",
            "Channels",
            display_name="Membrane",
            icon="ai-membrane.svg",
            description="Create a virtual graphite membrane slab and run MLP hydrophobic analysis",
        ),
        provider(
            "ai-analysis-pisa",
            "AI",
            "Sites",
            display_name="PISA",
            icon="pisa-logo.svg",
            description="Measure and highlight chain-chain interface buried surface area",
        ),
        provider(
            "ai-analysis-metal",
            "AI",
            "Sites",
            display_name="Metal",
            icon="ai-metal.svg",
            description="Review existing metals and predicted metal-binding candidates",
        ),
        provider(
            "ai-analysis-similar",
            "AI",
            "Structure",
            display_name="Similar",
            icon="foldseek-logo.png",
            description="Run Foldseek Similar Structures and align top hits",
        ),
        provider(
            "ai-analysis-foldmason",
            "AI",
            "Structure",
            display_name="FoldMason",
            icon="foldmason-logo.png",
            description="Export open structures and launch FoldMason multiple structure alignment",
        ),
        provider(
            "ai-analysis-dali",
            "AI",
            "Structure",
            display_name="DALI",
            icon="dali-logo.svg",
            description="Export current/selected structure and open the DALI structure-comparison server",
        ),
        provider(
            "ai-analysis-vast",
            "AI",
            "Structure",
            display_name="VAST",
            icon="vast-logo.svg",
            description="Export current/selected structure and open NCBI VAST",
        ),
        provider(
            "ai-analysis-pdbefold",
            "AI",
            "Structure",
            display_name="PDBeFold",
            icon="pdbefold-logo.png",
            description="Export current/selected structure and open PDBeFold / SSM",
        ),
        provider(
            "ai-analysis-usalign",
            "AI",
            "Structure",
            display_name="US-align",
            icon="usalign-logo.svg",
            description="Align open structures in ChimeraX and run local US-align if available",
        ),
        provider(
            "ai-analysis-rmsd",
            "AI",
            "Structure",
            display_name="RMSD",
            icon="ai-rmsd.svg",
            description="Pick a reference structure and compute pruned + native (full-pair) RMSD against multiple targets",
        ),
        provider(
            "ai-analysis-structalign",
            "AI",
            "Structure",
            display_name="StructMSA",
            icon="ai-sequence-bar.svg",
            description="Export an ESPript-like structural MSA report with core/loop, conservation, and metal-near tracks",
        ),
        provider(
            "ai-nucleotide-dock",
            "Nucleotides",
            "AI Tools",
            display_name="NucDock",
            icon="hdock-logo.png",
            description="Paste DNA/RNA sequence and open HDOCK with the current receptor structure",
        ),
        provider(
            "ai-nucleotide-afcomplex",
            "Nucleotides",
            "AI Tools",
            display_name="AF Complex",
            icon="ai-alphafold.svg",
            description="Paste DNA/RNA sequence and open AlphaFold Server with current protein chains",
        ),
        provider(
            "ai-nucleotide-boltz",
            "Nucleotides",
            "AI Tools",
            display_name="Boltz",
            icon="boltz-logo.svg",
            description="Run official latest Boltz CLI on current protein/DNA/RNA chains",
        ),
        provider(
            "ai-nucleotide-folddisco",
            "Nucleotides",
            "AI Tools",
            display_name="FoldDisco",
            icon="folddisco-logo.png",
            description="Search a selected structural motif with FoldDisco",
        ),
        provider(
            "ai-nucleotide-foldmason",
            "Nucleotides",
            "AI Tools",
            display_name="FoldMason",
            icon="foldmason-logo.png",
            description="Export open structures and launch FoldMason multiple structure alignment",
        ),
        provider(
            "ai-display-controls",
            "Molecule Display",
            "Helper",
            display_name="Display Ctrl",
            icon="display-controls.svg",
            description="Open sliders for selected transparency and cartoon helix/sheet thickness",
        ),
        provider(
            "ai-sequence-bar",
            "Molecule Display",
            "Helper",
            display_name="Sequence",
            icon="ai-sequence-bar.svg",
            description="Toggle the top Sequence display bar (clickable single-letter sequence)",
        ),
        provider(
            "ai-action-pad",
            "Molecule Display",
            "Helper",
            display_name="Action Pad",
            icon="ai-action-pad.svg",
            description="Open the PyMOL-style Action Pad for the current selection and models",
        ),
        provider(
            "ai-camera-bookmarks",
            "Molecule Display",
            "Helper",
            display_name="Bookmarks",
            icon="ai-camera-bookmarks.svg",
            description="Save / restore camera angles for screenshots",
        ),
        provider(
            "ai-assistant-open",
            "Molecule Display",
            "Helper",
            display_name="AI",
            icon="ai-assistant.svg",
            description="Open the AI Assistant workspace as a dock tab",
        ),
    ]

    def reset_codex_toolbar_entries():
        toolbar_data = getattr(toolbar, "_toolbar", None)
        if not isinstance(toolbar_data, dict):
            return
        ai_sections = toolbar_data.get("AI")
        stale_ai_sections = {"Quick", "Analysis", "Sequence", "Modeling", "Sites", "Channels", "Structure"}
        if isinstance(ai_sections, dict):
            for section in stale_ai_sections:
                ai_sections.pop(section, None)
            layout = ai_sections.get("__layout__")
            if isinstance(layout, dict):
                for section in stale_ai_sections:
                    layout.pop(section, None)
                for children in layout.values():
                    if hasattr(children, "difference_update"):
                        children.difference_update(stale_ai_sections)
        for tab_sections in toolbar_data.values():
            if not isinstance(tab_sections, dict):
                continue
            for section_dict in tab_sections.values():
                if not isinstance(section_dict, dict):
                    continue
                for display_name, entry in list(section_dict.items()):
                    if display_name == "__layout__":
                        continue
                    try:
                        provider_name, provider_bundle = entry[0], entry[1]
                    except Exception:
                        continue
                    bundle_name = getattr(provider_bundle, "name", "")
                    if bundle_name == "ChimeraX-CodexBridge" or str(provider_name).startswith("ai-"):
                        section_dict.pop(display_name, None)

    reset_codex_toolbar_entries()
    for name, kwargs in providers:
        try:
            toolbar.add_provider(bundle_info, name, **kwargs)
        except Exception:
            pass
    try:
        from chimerax.toolbar.tool import get_toolbar_singleton

        toolbar_tool = get_toolbar_singleton(session, create=False)
        if toolbar_tool is not None:
            current_version = getattr(session, "_codex_bridge_toolbar_runtime_version", 0)
            needs_rebuild = force_rebuild or current_version != toolbar_version
            if needs_rebuild:
                # Capture which tab the user is currently on so we can restore
                # it after _build_tabs() resets to tab 0 (Home). This avoids
                # the visible "AI tab -> Home flash" when the rebuild happens
                # after the user has already clicked AI.
                prev_tab_index = None
                try:
                    ttb = toolbar_tool.ttb
                    if hasattr(ttb, "currentIndex"):
                        prev_tab_index = ttb.currentIndex()
                    elif hasattr(ttb, "tabs") and hasattr(ttb.tabs, "currentIndex"):
                        prev_tab_index = ttb.tabs.currentIndex()
                except Exception:
                    prev_tab_index = None
                toolbar_tool.ttb.clear_all()
            else:
                try:
                    from .runtime_patches import style_ai_toolbar

                    style_ai_toolbar(session)
                except Exception:
                    pass
                return
            toolbar_tool._build_tabs()
            # Restore the user's active tab (if they were already on AI etc.)
            if needs_rebuild and prev_tab_index is not None and prev_tab_index >= 0:
                try:
                    ttb = toolbar_tool.ttb
                    if hasattr(ttb, "setCurrentIndex"):
                        ttb.setCurrentIndex(prev_tab_index)
                    elif hasattr(ttb, "tabs") and hasattr(ttb.tabs, "setCurrentIndex"):
                        ttb.tabs.setCurrentIndex(prev_tab_index)
                except Exception:
                    pass
            try:
                from .runtime_patches import style_ai_toolbar

                style_ai_toolbar(session)
            except Exception:
                pass
            session._codex_bridge_toolbar_runtime_installed = True
            session._codex_bridge_toolbar_runtime_version = toolbar_version
        else:
            from Qt.QtCore import QTimer

            for delay in (750, 2000, 5000):
                QTimer.singleShot(delay, lambda ses=session: _install_runtime_toolbar_buttons(ses, force_rebuild=True))
    except Exception:
        pass


def _ensure_sequence_bar_visible(session):
    """Idempotently open and display the Codex Sequence Bar.

    Called from several delayed retries so the bar always ends up visible
    on session start regardless of which UI event happens to fire first
    (the "ready" trigger isn't always reliable across ChimeraX versions /
    session-restore paths).
    """
    try:
        if not session.ui.is_gui:
            return
    except Exception:
        return
    try:
        from .sequence_bar import CodexSequenceBar

        sequence_bar = CodexSequenceBar.get_singleton(session, create=True, display=True)
        if sequence_bar is not None:
            try:
                sequence_bar.display(True)
            except Exception:
                pass
            try:
                sequence_bar.refresh()
            except Exception:
                pass
    except Exception:
        pass


def _auto_open_workspace(session):
    if not session.ui.is_gui:
        return
    if getattr(session, "_codex_bridge_auto_open_done", False):
        return
    session._codex_bridge_auto_open_done = True

    from Qt.QtCore import QTimer

    def open_later():
        assistant = None
        main_window = getattr(session.ui, "main_window", None)
        if main_window is not None:
            try:
                main_window.setUpdatesEnabled(False)
            except Exception:
                main_window = None
        try:
            from chimerax.cmd_line.tool import CommandLine

            CommandLine.get_singleton(session, create=True, display=True)
        except Exception:
            pass
        try:
            from .tool import CodexAssistant

            assistant = CodexAssistant.get_singleton(session)
            if assistant is not None:
                assistant.display(True)
                assistant._show_assistant_tab()
                assistant._focus_prompt()
                try:
                    assistant._apply_dock_fraction()
                except Exception:
                    pass
        except Exception:
            pass
        _ensure_display_controls_tab(session)
        _ensure_sequence_bar_visible(session)
        if main_window is not None:
            try:
                main_window.setUpdatesEnabled(True)
            except Exception:
                pass
        for delay in (0, 250, 800):
            QTimer.singleShot(delay, lambda ses=session, tool=assistant: _apply_startup_layout(ses, tool))
        for delay in (0, 300, 900, 2500, 5000):
            QTimer.singleShot(delay, lambda ses=session: _install_runtime_toolbar_buttons(ses, force_rebuild=True))
        # Belt-and-braces: re-issue the sequence bar open at several later
        # ticks too. If anything (session-restore, layout reshuffles,
        # another tool's startup hook) hides it after our first open, the
        # later retries put it back.
        for delay in (250, 1000, 2500, 5000):
            QTimer.singleShot(delay, lambda ses=session: _ensure_sequence_bar_visible(ses))
        # Keep the right dock as Log on top and helper tools as tabs below it.
        _schedule_helper_dock_layout(session, raise_tool="models")

    QTimer.singleShot(150, open_later)


class _MyAPI(BundleAPI):

    api_version = 1

    @staticmethod
    def initialize(session, bundle_info):
        from .integration import initialize_command_history, initialize_command_line_integration
        from .pick_mode import install_pick_modes
        from .runtime_patches import apply_runtime_patches
        from .semantic import initialize_semantic_cache

        apply_runtime_patches(session)
        initialize_semantic_cache(session)
        initialize_command_history(session)
        initialize_command_line_integration(session)
        install_pick_modes(session)
        # Always force-rebuild the AI toolbar at startup so SVG/displayName
        # changes don't get masked by a session-cached toolbar version.
        _install_runtime_toolbar_buttons(session, force_rebuild=True)
        if not hasattr(session, "_codex_bridge_models_removed_handler"):
            from .session_state import install_session_state_cleanup
            install_session_state_cleanup(session)
        if not hasattr(session, "_codex_bridge_chem_restore_handler"):
            from .display_color import install_auto_chemistry_restore
            install_auto_chemistry_restore(session)
        if session.ui.is_gui and not hasattr(session, "_codex_bridge_auto_open_handler"):
            session._codex_bridge_auto_open_handler = session.ui.triggers.add_handler(
                "ready",
                lambda *_args, ses=session: _auto_open_workspace(ses),
            )
        # Independent of the "ready" trigger, schedule a few QTimer ticks
        # that force the Sequence Bar visible. Some session-restore paths
        # don't emit "ready", in which case _auto_open_workspace never
        # runs -- this guarantees the bar still ends up displayed.
        if session.ui.is_gui:
            try:
                from Qt.QtCore import QTimer

                for delay in (300, 1200, 3000):
                    QTimer.singleShot(
                        delay,
                        lambda ses=session: _ensure_sequence_bar_visible(ses),
                    )
            except Exception:
                pass

    @staticmethod
    def run_provider(session, name, mgr, **kw):
        from .toolbar_actions import run_toolbar_action

        run_toolbar_action(session, name)

    @staticmethod
    def register_command(bi, ci, logger):
        from . import cmd
        from chimerax.core.commands import register

        if ci.name == "ai":
            desc = cmd.ai_desc
            func = cmd.ai
        elif ci.name == "codex ask":
            desc = cmd.codex_ask_desc
            func = cmd.codex_ask
        elif ci.name == "codex backend":
            desc = cmd.codex_backend_desc
            func = cmd.codex_backend
        elif ci.name == "codex model":
            desc = cmd.codex_model_desc
            func = cmd.codex_model
        elif ci.name == "codex effort":
            desc = cmd.codex_effort_desc
            func = cmd.codex_effort
        elif ci.name == "codex auto":
            desc = cmd.codex_auto_desc
            func = cmd.codex_auto
        elif ci.name == "codex context":
            desc = cmd.codex_context_desc
            func = cmd.codex_context
        elif ci.name == "codex selftest":
            desc = cmd.codex_selftest_desc
            func = cmd.codex_selftest
        elif ci.name == "codex routing":
            desc = cmd.codex_routing_desc
            func = cmd.codex_routing
        elif ci.name == "codex tool":
            desc = cmd.codex_tool_desc
            func = cmd.codex_tool
        elif ci.name == "codex actions":
            desc = cmd.codex_actions_desc
            func = cmd.codex_actions
        elif ci.name == "codex seqbar":
            desc = cmd.codex_seqbar_desc
            func = cmd.codex_seqbar
        elif ci.name == "codex structalign":
            desc = cmd.codex_structalign_desc
            func = cmd.codex_structalign
        elif ci.name == "signalp":
            desc = cmd.signalp_desc
            func = cmd.signalp
        elif ci.name in ("metal place", "metal predict", "metal evidence", "metal clear"):
            from . import metal_placement
            metal_placement.register_metal_commands(bi, ci.name, logger)
            return
        else:
            raise ValueError("trying to register unknown command: %s" % ci.name)

        if desc.synopsis is None:
            desc.synopsis = ci.synopsis

        register(ci.name, desc, func)

    @staticmethod
    def start_tool(session, bi, ti):
        if ti.name == "AI Assistant":
            from .tool import CodexAssistant
            return CodexAssistant.get_singleton(session)
        if ti.name == "Action Pad":
            from .action_pad import CodexActionPad
            return CodexActionPad.get_singleton(session)
        if ti.name == "Sequence Bar":
            from .sequence_bar import CodexSequenceBar
            return CodexSequenceBar.get_singleton(session)
        if ti.name == "Display Controls":
            from .display_controls import CodexDisplayControls
            return CodexDisplayControls.get_singleton(session)
        if ti.name == "CAVER":
            from .caver import CodexCaverTool
            return CodexCaverTool.get_singleton(session)
        if ti.name == "Camera Bookmarks":
            from .camera_bookmarks import CameraBookmarks
            return CameraBookmarks(session, ti.name)
        raise ValueError("trying to start unknown tool: %s" % ti.name)

    @staticmethod
    def get_class(class_name):
        if class_name == "CodexAssistant":
            from .tool import CodexAssistant
            return CodexAssistant
        if class_name == "CodexActionPad":
            from .action_pad import CodexActionPad
            return CodexActionPad
        if class_name == "CodexSequenceBar":
            from .sequence_bar import CodexSequenceBar
            return CodexSequenceBar
        if class_name == "CodexDisplayControls":
            from .display_controls import CodexDisplayControls
            return CodexDisplayControls
        if class_name == "CodexCaverTool":
            from .caver import CodexCaverTool
            return CodexCaverTool
        if class_name == "CameraBookmarks":
            from .camera_bookmarks import CameraBookmarks
            return CameraBookmarks
        return None


bundle_api = _MyAPI()
