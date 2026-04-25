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
        from Qt.QtWidgets import QAbstractScrollArea, QLayout, QSizePolicy, QWidget
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
            try:
                widget.setMinimumSize(0, 0)
                widget.setMinimumHeight(0)
                widget.setMaximumHeight(16777215)
            except Exception:
                pass

            if aggressive:
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

    providers = [
        (
            "ai-nucleotide-dock",
            {
                "tab": "Nucleotides",
                "section": "AI Tools",
                "display_name": "NucDock",
                "icon": "hdock-logo.png",
                "description": "Paste DNA/RNA sequence and open HDOCK with the current receptor structure",
            },
        ),
        (
            "ai-nucleotide-afcomplex",
            {
                "tab": "Nucleotides",
                "section": "AI Tools",
                "display_name": "AF Complex",
                "icon": "alphafold-logo.png",
                "description": "Paste DNA/RNA sequence and open AlphaFold Server with current protein chains",
            },
        ),
        (
            "ai-nucleotide-boltz",
            {
                "tab": "Nucleotides",
                "section": "AI Tools",
                "display_name": "Boltz",
                "icon": "boltz-logo.svg",
                "description": "Run official latest Boltz CLI on current protein/DNA/RNA chains",
            },
        ),
        (
            "ai-analysis-nucdock",
            {
                "tab": "AI",
                "section": "Analysis",
                "display_name": "NucDock",
                "icon": "hdock-logo.png",
                "description": "Paste DNA/RNA sequence and dock it against the current receptor with HDOCK",
            },
        ),
        (
            "ai-analysis-afcomplex",
            {
                "tab": "AI",
                "section": "Analysis",
                "display_name": "AF Complex",
                "icon": "alphafold-logo.png",
                "description": "Paste DNA/RNA sequence and open AlphaFold Server with current protein chains",
            },
        ),
        (
            "ai-analysis-boltz",
            {
                "tab": "AI",
                "section": "Analysis",
                "display_name": "Boltz",
                "icon": "boltz-logo.svg",
                "description": "Run official latest Boltz CLI on current chains, or open native Boltz panel",
            },
        ),
        (
            "ai-analysis-hhpred",
            {
                "tab": "AI",
                "section": "Analysis",
                "display_name": "HHpred",
                "icon": "hhpred-logo.svg",
                "description": "Open HHpred / HHblits with the current protein sequence",
            },
        ),
        (
            "ai-analysis-catalytic",
            {
                "tab": "AI",
                "section": "Analysis",
                "display_name": "Catalytic",
                "icon": "ai-site.svg",
                "description": "Rank and highlight catalytic residue candidates",
            },
        ),
        (
            "ai-analysis-membrane",
            {
                "tab": "AI",
                "section": "Analysis",
                "display_name": "Membrane",
                "icon": "ai-membrane.svg",
                "description": "Create a virtual graphite membrane slab and run MLP hydrophobic analysis",
            },
        ),
        (
            "ai-analysis-pisa",
            {
                "tab": "AI",
                "section": "Analysis",
                "display_name": "PISA",
                "icon": "pisa-logo.svg",
                "description": "Measure and highlight chain-chain interface buried surface area",
            },
        ),
        (
            "ai-analysis-dali",
            {
                "tab": "AI",
                "section": "Structure",
                "display_name": "DALI",
                "icon": "dali-logo.png",
                "description": "Export current/selected structure and open the DALI structure-comparison server",
            },
        ),
        (
            "ai-analysis-vast",
            {
                "tab": "AI",
                "section": "Structure",
                "display_name": "VAST",
                "icon": "vast-logo.png",
                "description": "Export current/selected structure and open NCBI VAST",
            },
        ),
        (
            "ai-analysis-pdbefold",
            {
                "tab": "AI",
                "section": "Structure",
                "display_name": "PDBeFold",
                "icon": "pdbefold-logo.png",
                "description": "Export current/selected structure and open PDBeFold / SSM",
            },
        ),
        (
            "ai-analysis-usalign",
            {
                "tab": "AI",
                "section": "Structure",
                "display_name": "US-align",
                "icon": "usalign-logo.png",
                "description": "Export two or more structures and open US-align / TM-score alignment",
            },
        ),
        (
            "ai-analysis-folddisco",
            {
                "tab": "Nucleotides",
                "section": "AI Tools",
                "display_name": "FoldDisco",
                "icon": "folddisco-logo.png",
                "description": "Search a selected structural motif with FoldDisco",
            },
        ),
        (
            "ai-analysis-foldmason",
            {
                "tab": "Nucleotides",
                "section": "AI Tools",
                "display_name": "FoldMason",
                "icon": "foldmason-logo.png",
                "description": "Export open structures and launch FoldMason multiple structure alignment",
            },
        ),
        (
            "ai-display-controls",
            {
                "tab": "Molecule Display",
                "section": "Adjust",
                "display_name": "Display Ctrl",
                "icon": "display-controls.svg",
                "description": "Open sliders for selected transparency and cartoon helix/sheet thickness",
            },
        ),
    ]
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
