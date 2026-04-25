from chimerax.mouse_modes import MouseMode

from .display_color import apply_stick_context_colors
from .integration import command_batch


class _BaseCodexPickMode(MouseMode):

    minimum_drag_pixels = 5
    mode_label = "pick"
    drag_delegate_mode_name = "rotate"

    def __init__(self, session):
        MouseMode.__init__(self, session)
        self._drag_delegate_mode = None
        self._drag_started = False

    def mouse_down(self, event):
        MouseMode.mouse_down(self, event)
        self._drag_started = False
        self._drag_delegate_mode = self._resolve_drag_delegate_mode()
        if self._drag_delegate_mode is not None:
            try:
                self._drag_delegate_mode.mouse_down(event)
            except Exception:
                self._drag_delegate_mode = None

    def mouse_drag(self, event):
        if self._drag_delegate_mode is None:
            return
        if not self._drag_started and not self._is_drag_event(event):
            return
        self._drag_started = True
        self._drag_delegate_mode.mouse_drag(event)

    def mouse_up(self, event):
        is_click = not self._drag_started and self._is_click_event(event)
        if self._drag_delegate_mode is not None:
            try:
                if self._drag_started:
                    self._drag_delegate_mode.mouse_up(event)
                else:
                    MouseMode.mouse_up(self._drag_delegate_mode, event)
            except Exception:
                pass
        self._drag_delegate_mode = None
        self._drag_started = False
        if is_click and not self.double_click:
            self._pick_and_select(event)
        MouseMode.mouse_up(self, event)

    def _resolve_drag_delegate_mode(self):
        mode_name = getattr(self, "drag_delegate_mode_name", None)
        if not mode_name:
            return None
        try:
            return self.session.ui.mouse_modes.named_mode(mode_name)
        except Exception:
            return None

    def _is_drag_event(self, event):
        down_pos = self.mouse_down_position
        up_pos = event.position()
        if down_pos is None:
            return False
        dx = up_pos[0] - down_pos[0]
        dy = up_pos[1] - down_pos[1]
        return (dx * dx + dy * dy) > (self.minimum_drag_pixels * self.minimum_drag_pixels)

    def _is_click_event(self, event):
        return not self._is_drag_event(event)

    def _pick_and_select(self, event):
        x, y = event.position()
        pick = self.session.main_view.picked_object(x, y)
        residue = self._picked_residue(pick)
        if residue is None:
            from chimerax.core.commands import run

            with command_batch(self.session, f"Mouse {self.mode_label}:clear"):
                run(self.session, "select clear")
            self.session.logger.status(f"{self.mode_label}: cleared selection")
            return
        spec = self._selection_spec(residue)
        if not spec:
            self.session.logger.status(f"{self.mode_label}: no selectable target")
            return
        from chimerax.core.commands import run

        with command_batch(self.session, f"Mouse {self.mode_label}:{spec}"):
            run(self.session, f"select {spec}")
        self.session.logger.status(f"{self.mode_label}: {spec}")

    def _picked_residue(self, pick):
        from chimerax.atomic import PickedAtom, PickedResidue

        if isinstance(pick, PickedAtom):
            return pick.atom.residue
        if isinstance(pick, PickedResidue):
            return pick.residue
        if hasattr(pick, "atom"):
            return pick.atom.residue
        if hasattr(pick, "residue"):
            return pick.residue
        return None

    def _selection_spec(self, residue):
        model = getattr(residue, "structure", None)
        model_spec = f"#{getattr(model, 'id_string', '?')}"
        chain_id = str(getattr(residue, "chain_id", "")).strip() or "?"
        number = int(getattr(residue, "number", 0))
        return self._format_spec(model_spec, chain_id, number)

    def _format_spec(self, model_spec, chain_id, number):
        raise NotImplementedError()


class CodexResiduePickMode(_BaseCodexPickMode):
    name = "codex residue pick"
    mode_label = "residue pick"

    def _format_spec(self, model_spec, chain_id, number):
        return f"{model_spec}/{chain_id}:{number}"


class CodexChainPickMode(_BaseCodexPickMode):
    name = "codex chain pick"
    mode_label = "chain pick"

    def _format_spec(self, model_spec, chain_id, number):
        return f"{model_spec}/{chain_id}"


class CodexContextMenuMode(_BaseCodexPickMode):
    name = "codex context menu"
    mode_label = "context menu"
    drag_delegate_mode_name = "translate"

    def _format_spec(self, model_spec, chain_id, number):
        return f"{model_spec}/{chain_id}:{number}"

    def _pick_and_select(self, event):
        x, y = event.position()
        residue = self._picked_residue(self.session.main_view.picked_object(x, y))
        if residue is None:
            self.session.logger.status("context menu: nothing picked")
            return

        model = getattr(residue, "structure", None)
        model_spec = f"#{getattr(model, 'id_string', '?')}"
        chain_id = str(getattr(residue, "chain_id", "")).strip() or "?"
        residue_number = int(getattr(residue, "number", 0))
        residue_spec = f"{model_spec}/{chain_id}:{residue_number}"
        chain_spec = f"{model_spec}/{chain_id}"

        from Qt.QtCore import QPoint
        from Qt.QtWidgets import QMenu

        menu = QMenu(self.session.ui.main_window)
        action_menu = menu.addMenu("Action")
        action_menu.addAction("Select residue", lambda: self._run_commands(f"Context residue:{residue_spec}", [f"select {residue_spec}"]))
        action_menu.addAction("Select chain", lambda: self._run_commands(f"Context chain:{chain_spec}", [f"select {chain_spec}"]))
        action_menu.addSeparator()
        action_menu.addAction("Focus residue", lambda: self._run_commands(f"Context focus residue:{residue_spec}", [f"select {residue_spec}", "view sel"]))
        action_menu.addAction("Focus chain", lambda: self._run_commands(f"Context focus chain:{chain_spec}", [f"select {chain_spec}", "view sel"]))

        show_menu = menu.addMenu("Show")
        show_menu.addAction("Sticks", lambda: self._show_sticks(residue_spec))
        show_menu.addAction("Surface", lambda: self._run_commands(f"Context surface:{residue_spec}", [f"surface {residue_spec}"]))
        show_menu.addAction("Chain surface", lambda: self._run_commands(f"Context chain surface:{chain_spec}", [f"surface {chain_spec}"]))
        show_menu.addAction("Cartoon chain", lambda: self._run_commands(f"Context cartoon:{chain_spec}", [f"cartoon {chain_spec}"]))

        hide_menu = menu.addMenu("Hide")
        hide_menu.addAction("Atoms", lambda: self._run_commands(f"Context hide atoms:{residue_spec}", [f"hide {residue_spec} atoms"]))
        hide_menu.addAction("Surface", lambda: self._run_commands(f"Context hide surface:{residue_spec}", [f"~surface {residue_spec}"]))
        hide_menu.addAction("Chain surface", lambda: self._run_commands(f"Context hide chain surface:{chain_spec}", [f"~surface {chain_spec}"]))
        hide_menu.addAction("Cartoon chain", lambda: self._run_commands(f"Context hide cartoon:{chain_spec}", [f"hide {chain_spec} cartoons"]))

        color_menu = menu.addMenu("Color")
        color_menu.addAction("Carbon context + hetero elements", lambda: self._apply_context_stick_colors(residue_spec))
        color_menu.addAction("By element", lambda: self._run_commands(f"Context color byelement:{residue_spec}", [f"color {residue_spec} byelement"]))
        color_menu.addAction("By chain", lambda: self._run_commands(f"Context color bychain:{chain_spec}", [f"color {chain_spec} bychain"]))
        color_menu.addAction("By model", lambda: self._run_commands(f"Context color bymodel:{model_spec}", [f"color {model_spec} bymodel"]))
        preset_menu = color_menu.addMenu("Preset")
        for color_name in ("yellow", "cyan", "magenta", "hotpink", "cornflowerblue", "orange", "gold"):
            preset_menu.addAction(color_name, lambda checked=False, c=color_name: self._run_commands(f"Context color {c}:{residue_spec}", [f"color {residue_spec} {c}"]))
        color_menu.addAction("Custom...", lambda: self._pick_custom_color(residue_spec))

        label_menu = menu.addMenu("Label")
        label_menu.addAction("Residue", lambda: self._run_commands(f"Context label:{residue_spec}", [f"label {residue_spec} residues"]))
        label_menu.addAction("Chain", lambda: self._run_commands(f"Context chain label:{chain_spec}", [f"label {chain_spec} residues"]))
        label_menu.addAction("Clear labels", lambda: self._run_commands("Context label clear", ["label delete"]))

        ai_menu = menu.addMenu("AI")
        ai_menu.addAction("Analyze residue", lambda: self._launch_ai_prompt(f"Analyze {residue_spec} with evidence and confidence.", "analyze"))
        ai_menu.addAction("Improve chain view", lambda: self._launch_ai_prompt(f"Improve the view for {chain_spec} and apply the changes directly.", "agent"))

        try:
            point = QPoint(*event.global_position())
        except Exception:
            point = self.session.ui.main_window.mapToGlobal(QPoint(x, y))
        self.session.ui.post_context_menu(menu, point)

    def _run_commands(self, batch_label, commands):
        from chimerax.core.commands import run

        with command_batch(self.session, batch_label):
            for command in commands:
                run(self.session, command)
        self.session.logger.status(batch_label)

    def _show_sticks(self, residue_spec):
        from chimerax.core.commands import run

        with command_batch(self.session, f"Context sticks:{residue_spec}"):
            run(self.session, f"show {residue_spec} atoms")
            run(self.session, f"style {residue_spec} stick")
            apply_stick_context_colors(self.session, residue_spec)
        self.session.logger.status(f"Context sticks:{residue_spec}")

    def _apply_context_stick_colors(self, residue_spec):
        with command_batch(self.session, f"Context stick colors:{residue_spec}"):
            apply_stick_context_colors(self.session, residue_spec)
        self.session.logger.status(f"Context stick colors:{residue_spec}")

    def _pick_custom_color(self, spec):
        from Qt.QtGui import QColor
        from Qt.QtWidgets import QColorDialog

        color = QColorDialog.getColor(QColor("#ffd166"), self.session.ui.main_window, "Choose ChimeraX Color")
        if not color.isValid():
            return
        self._run_commands(f"Context color custom:{spec}", [f"color {spec} {color.name()}"])


def install_pick_modes(session):
    if not session.ui.is_gui:
        return
    if (
        hasattr(session, "_codex_bridge_residue_pick_mode")
        and hasattr(session, "_codex_bridge_chain_pick_mode")
        and hasattr(session, "_codex_bridge_context_menu_mode")
    ):
        return

    mm = session.ui.mouse_modes
    try:
        mm.trackpad.enable_multitouch(True)
    except Exception:
        pass
    try:
        translate_mode = mm.named_mode("translate")
        if translate_mode is not None:
            for action_name in ("three finger swipe", "two finger swipe"):
                for modifiers in (["shift"], ["command"], ["shift", "command"]):
                    try:
                        mm.bind_mouse_mode(trackpad_action=action_name, trackpad_modifiers=modifiers, mode=translate_mode)
                    except Exception:
                        pass
            for action_name in ("three finger swipe", "two finger swipe"):
                for modifiers in (["control"], ["shift", "control"]):
                    try:
                        mm.remove_binding(trackpad_action=action_name, trackpad_modifiers=modifiers)
                    except Exception:
                        pass
    except Exception:
        pass
    residue_mode = CodexResiduePickMode(session)
    chain_mode = CodexChainPickMode(session)
    menu_mode = CodexContextMenuMode(session)
    mm.add_mode(residue_mode)
    mm.add_mode(chain_mode)
    mm.add_mode(menu_mode)
    session._codex_bridge_residue_pick_mode = residue_mode
    session._codex_bridge_chain_pick_mode = chain_mode
    session._codex_bridge_context_menu_mode = menu_mode
    if not getattr(session, "_codex_bridge_pick_mode_initialized", False):
        mm.bind_mouse_mode("left", [], residue_mode)
        mm.bind_mouse_mode("right", [], menu_mode)
        session._codex_bridge_pick_mode_initialized = True


def bind_pick_mode(session, which):
    install_pick_modes(session)
    mm = session.ui.mouse_modes
    if which == "residue":
        mm.bind_mouse_mode("left", [], session._codex_bridge_residue_pick_mode)
        return "Left click now picks residues."
    if which == "chain":
        mm.bind_mouse_mode("left", [], session._codex_bridge_chain_pick_mode)
        return "Left click now picks chains."
    if which == "menu":
        mm.bind_mouse_mode("right", [], session._codex_bridge_context_menu_mode)
        return "Right click now opens the residue context menu."
    if which == "default":
        mm.bind_standard_mouse_modes(buttons=("left", "right"))
        return "Mouse buttons restored to default rotate/translate."
    raise ValueError(f"Unknown pick mode: {which}")
