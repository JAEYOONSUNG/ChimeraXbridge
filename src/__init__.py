from chimerax.core.toolshed import BundleAPI


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

    for dock_widget in tuple(dict.fromkeys(dock_widgets)):
        title = _dock_title(dock_widget).lower()
        if not any(token in title for token in compact_tokens):
            continue
        aggressive = any(token in title for token in aggressive_tokens)
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
                if protected_control:
                    # Keep explicit button/combo heights; only prevent collapse.
                    widget.setMinimumHeight(24)
                else:
                    widget.setMinimumSize(0, 0)
                    widget.setMinimumHeight(0)
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

    action_dock = getattr(getattr(action_pad, "tool_window", None), "_dock_widget", None)
    models_dock = _find_dock_widget(session, ("models", "model panel"))
    if action_dock is None or models_dock is None or action_dock is models_dock:
        return action_pad
    try:
        main_window.tabifyDockWidget(models_dock, action_dock)
        if raise_action:
            action_dock.raise_()
        else:
            models_dock.raise_()
    except Exception:
        pass
    return action_pad


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

    right_docks = _right_side_docks(session)
    if right_docks:
        target_width = max(360, int(max(main_window.width(), 900) * 0.30))
        try:
            main_window.resizeDocks(right_docks, [target_width] * len(right_docks), Qt.Orientation.Horizontal)
        except Exception:
            pass
        _release_dock_constraints(right_docks)

    log_dock = _find_dock_widget(session, ("log",))
    models_dock = _find_dock_widget(session, ("models", "model panel"))
    assistant_dock = _find_dock_widget(session, ("ai assistant",))
    vertical_docks = [dock for dock in (log_dock, models_dock, assistant_dock) if dock is not None]
    if len(vertical_docks) >= 2:
        available_height = max(main_window.height(), 800)
        height_targets = []
        for dock in vertical_docks:
            if dock is assistant_dock:
                height_targets.append(int(available_height * 0.58))
            elif dock is models_dock:
                height_targets.append(int(available_height * 0.10))
            else:
                height_targets.append(int(available_height * 0.09))
        try:
            main_window.resizeDocks(vertical_docks, height_targets, Qt.Orientation.Vertical)
        except Exception:
            pass
        _release_dock_constraints(vertical_docks)


def _install_runtime_toolbar_buttons(session, force_rebuild=False):
    if not getattr(session.ui, "is_gui", False):
        return
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
        provider("ai-layout-structure", "AI", "Structure", after="Sites"),
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
            display_name="Site",
            icon="ai-site.svg",
            description="Focus the most likely site or interface",
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
            "ai-analysis-blast",
            "AI",
            "Sequence",
            display_name="Blast",
            icon="blast-logo.png",
            description="Open sequence-analysis site chooser for the current protein sequence",
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
            "ai-analysis-conserve",
            "AI",
            "Sequence",
            display_name="Consurf",
            icon="consurf-logo.png",
            description="Open the ConSurf Colab workflow for the current sequence",
        ),
        provider(
            "ai-analysis-alphafold",
            "AI",
            "Modeling",
            display_name="AlphaFold",
            icon="alphafold-logo.png",
            description="Open AlphaFold Server for the current protein sequence(s)",
        ),
        provider(
            "ai-analysis-afcomplex",
            "AI",
            "Modeling",
            display_name="AF Complex",
            icon="alphafold-logo.png",
            description="Paste DNA/RNA sequence and open AlphaFold Server with current protein chains",
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
            "ai-analysis-nucdock",
            "AI",
            "Modeling",
            display_name="NucDock",
            icon="hdock-logo.png",
            description="Paste DNA/RNA sequence and dock it against the current receptor with HDOCK",
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
            "Sites",
            display_name="CAVER",
            icon="caverweb-logo.svg",
            description="Export current/selected structure and open CAVER Web tunnel/channel analysis",
        ),
        provider(
            "ai-analysis-membrane",
            "AI",
            "Sites",
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
            description="Export two or more structures and open US-align / TM-score alignment",
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
            icon="alphafold-logo.png",
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
            "Adjust",
            display_name="Display Ctrl",
            icon="display-controls.svg",
            description="Open sliders for selected transparency and cartoon helix/sheet thickness",
        ),
    ]

    def reset_codex_toolbar_entries():
        toolbar_data = getattr(toolbar, "_toolbar", None)
        if not isinstance(toolbar_data, dict):
            return
        ai_sections = toolbar_data.get("AI")
        stale_ai_sections = {"Quick", "Analysis", "Sequence", "Modeling", "Sites", "Structure"}
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
            if force_rebuild or not getattr(session, "_codex_bridge_toolbar_runtime_installed", False):
                toolbar_tool.ttb.clear_all()
            else:
                return
            toolbar_tool._build_tabs()
            try:
                from .runtime_patches import style_ai_toolbar

                style_ai_toolbar(session)
            except Exception:
                pass
            session._codex_bridge_toolbar_runtime_installed = True
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
        try:
            from .sequence_bar import CodexSequenceBar

            sequence_bar = CodexSequenceBar.get_singleton(session)
            if sequence_bar is not None:
                sequence_bar.display(True)
                sequence_bar.refresh()
        except Exception:
            pass
        finally:
            if main_window is not None:
                try:
                    main_window.setUpdatesEnabled(True)
                except Exception:
                    pass
        for delay in (0, 250, 800):
            QTimer.singleShot(delay, lambda ses=session, tool=assistant: _apply_startup_layout(ses, tool))
        for delay in (0, 300, 900):
            QTimer.singleShot(delay, lambda ses=session: _install_runtime_toolbar_buttons(ses))

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
        _install_runtime_toolbar_buttons(session)
        if session.ui.is_gui and not hasattr(session, "_codex_bridge_auto_open_handler"):
            session._codex_bridge_auto_open_handler = session.ui.triggers.add_handler(
                "ready",
                lambda *_args, ses=session: _auto_open_workspace(ses),
            )

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
        return None


bundle_api = _MyAPI()
