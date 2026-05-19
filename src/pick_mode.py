from chimerax.mouse_modes import MouseMode

from .display_color import (
    apply_stick_context_colors,
    hide_sticks_preserving_sidechains,
    restore_charge_colors,
    show_sticks_with_cartoon_anchor,
)
from .integration import command_batch


PICK_MODE_VERSION = 8

_CODEX_MOUSE_MODE_NAMES = {
    "codex residue pick",
    "codex residue add pick",
    "codex chain pick",
    "codex chain add pick",
    "codex context menu",
}


class _BaseCodexPickMode(MouseMode):

    minimum_drag_pixels = 5
    mode_label = "pick"
    drag_delegate_mode_name = "rotate"
    selection_mode = "replace"

    def __init__(self, session):
        MouseMode.__init__(self, session)
        self._codex_bridge_pick_mode_version = PICK_MODE_VERSION
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
        additive = self._additive_selection(event)
        if residue is None:
            if additive:
                self.session.logger.status(f"{self.mode_label}: nothing picked; selection kept")
                return
            from chimerax.core.commands import run

            with command_batch(self.session, f"Mouse {self.mode_label}:clear"):
                run(self.session, "select clear")
            self.session._codex_bridge_last_chain_selection_spec = ""
            self.session.logger.status(f"{self.mode_label}: cleared selection")
            return
        spec = self._selection_spec(residue)
        if not spec:
            self.session.logger.status(f"{self.mode_label}: no selectable target")
            return
        from chimerax.core.commands import run

        with command_batch(self.session, f"Mouse {self.mode_label}:{spec}"):
            command = f"select add {spec}" if additive else f"select {spec}"
            run(self.session, command)
        if isinstance(self, CodexChainPickMode):
            self.session._codex_bridge_last_chain_selection_spec = spec
        else:
            self.session._codex_bridge_last_chain_selection_spec = ""
        action = "added" if additive else "selected"
        self.session.logger.status(f"{self.mode_label}: {action} {spec}")

    def _additive_selection(self, event):
        if getattr(self, "selection_mode", "") == "add":
            return True
        try:
            return bool(event.shift_down())
        except Exception:
            return False

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

    def _launch_ai_prompt(self, prompt, mode="analyze"):
        """Send a free-text prompt to the AI assistant.
        Implemented here so context-menu modes don't depend on action_pad
        callback wiring."""
        from chimerax.core.commands import run as _cx_run
        try:
            _cx_run(self.session, "ui tool show 'AI Assistant'")
        except Exception:
            pass
        # Use the registered codex command. RestOfLine takes the rest of the
        # line raw, so we don't need to quote special characters.
        try:
            _cx_run(self.session, f"codex ask {prompt}")
        except Exception as exc:
            self.session.logger.warning(
                f"AI prompt could not be sent ({exc}). "
                "Open the AI Assistant tool manually and paste the prompt.")


class CodexResiduePickMode(_BaseCodexPickMode):
    name = "codex residue pick"
    mode_label = "residue pick"

    def _format_spec(self, model_spec, chain_id, number):
        return f"{model_spec}/{chain_id}:{number}"


class CodexResidueAddPickMode(CodexResiduePickMode):
    name = "codex residue add pick"
    mode_label = "residue add pick"
    drag_delegate_mode_name = "translate"
    selection_mode = "add"


class CodexChainPickMode(_BaseCodexPickMode):
    name = "codex chain pick"
    mode_label = "chain pick"

    def _format_spec(self, model_spec, chain_id, number):
        return f"{model_spec}/{chain_id}"


class CodexChainAddPickMode(CodexChainPickMode):
    name = "codex chain add pick"
    mode_label = "chain add pick"
    drag_delegate_mode_name = "translate"
    selection_mode = "add"


class CodexContextMenuMode(_BaseCodexPickMode):
    name = "codex context menu"
    mode_label = "context menu"
    drag_delegate_mode_name = "zoom"

    def _format_spec(self, model_spec, chain_id, number):
        return f"{model_spec}/{chain_id}:{number}"

    def _pick_and_select(self, event):
        x, y = event.position()
        residue = self._picked_residue(self.session.main_view.picked_object(x, y))
        if residue is None:
            # Empty-space right-click: if something is currently selected,
            # show a context menu that operates on the existing selection.
            sel_residues = self._selected_residues_from_session()
            if len(sel_residues) > 0:
                self._show_selection_menu(event, sel_residues)
                return
            self.session.logger.status("context menu: nothing picked, nothing selected")
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
        action_menu.addSeparator()
        action_menu.addAction("Delete residue atoms...", lambda: self._delete_target_atoms(residue_spec, residue_spec))
        action_menu.addAction("Delete chain atoms...", lambda: self._delete_target_atoms(chain_spec, chain_spec))
        action_menu.addAction("Keep only this chain in model...", lambda: self._keep_only_chain(chain_spec))
        action_menu.addSeparator()
        action_menu.addAction(
            "Delete waters / solvent in model...",
            lambda checked=False, ms=model_spec: self._run_solvent_action("delete", ms),
        )
        action_menu.addAction(
            "Delete waters / solvent in all models...",
            lambda checked=False: self._run_solvent_action("delete", None),
        )

        show_menu = menu.addMenu("Show")
        show_menu.addAction("Atoms (residue)", lambda: self._run_commands(f"Context show atoms:{residue_spec}", [f"show {residue_spec} atoms"]))
        show_menu.addAction("Sticks (residue)", lambda: self._show_sticks(residue_spec))
        show_menu.addAction("Cartoon (residue)", lambda: self._run_commands(f"Context show cartoon:{residue_spec}", [f"show {residue_spec} cartoons"]))
        show_menu.addAction("Surface (residue)", lambda: self._run_commands(f"Context surface:{residue_spec}", [f"surface {residue_spec}"]))
        show_menu.addSeparator()
        show_menu.addAction("Cartoon (chain)", lambda: self._run_commands(f"Context cartoon:{chain_spec}", [f"cartoon {chain_spec}"]))
        show_menu.addAction("Surface (chain)", lambda: self._run_commands(f"Context chain surface:{chain_spec}", [f"surface {chain_spec}"]))
        self._add_solvent_display_actions(show_menu, "show", model_spec)

        hide_menu = menu.addMenu("Hide")
        hide_menu.addAction("Atoms (residue)", lambda: self._run_commands(f"Context hide atoms:{residue_spec}", [f"hide {residue_spec} atoms"]))
        hide_menu.addAction("Sticks (residue)", lambda: self._hide_sticks(residue_spec, forget=True))
        hide_menu.addAction("Cartoon (residue)", lambda: self._run_commands(f"Context hide cartoon:{residue_spec}", [f"hide {residue_spec} cartoons"]))
        hide_menu.addAction("Surface (residue)", lambda: self._run_commands(f"Context hide surface:{residue_spec}", [f"~surface {residue_spec}"]))
        hide_menu.addAction(
            "Everything (residue)",
            lambda: self._run_commands(
                f"Context hide all:{residue_spec}",
                [
                    f"hide {residue_spec} atoms",
                    f"hide {residue_spec} cartoons",
                    f"~surface {residue_spec}",
                ],
            ),
        )
        hide_menu.addSeparator()
        hide_menu.addAction("Sticks (chain)", lambda: self._hide_sticks(chain_spec, preserve_marked=True))
        hide_menu.addAction("Cartoon (chain)", lambda: self._run_commands(f"Context hide cartoon:{chain_spec}", [f"hide {chain_spec} cartoons"]))
        hide_menu.addAction("Surface (chain)", lambda: self._run_commands(f"Context hide chain surface:{chain_spec}", [f"~surface {chain_spec}"]))
        self._add_solvent_display_actions(hide_menu, "hide", model_spec)

        color_menu = menu.addMenu("Color")
        color_menu.addAction("Carbon context + hetero elements", lambda: self._apply_context_stick_colors(residue_spec))
        color_menu.addAction("By element", lambda: self._run_commands(f"Context color byelement:{residue_spec}", [f"color {residue_spec} byelement"]))
        color_menu.addAction("By chain", lambda: self._color_and_restore(f"Context color bychain:{chain_spec}", chain_spec, "bychain"))
        color_menu.addAction("By model", lambda: self._color_and_restore(f"Context color bymodel:{model_spec}", model_spec, "bymodel"))
        preset_menu = color_menu.addMenu("Preset")
        for color_name in ("yellow", "cyan", "magenta", "hotpink", "cornflowerblue", "orange", "gold"):
            preset_menu.addAction(color_name, lambda checked=False, c=color_name: self._color_and_restore(f"Context color {c}:{residue_spec}", residue_spec, c))
        color_menu.addAction("Custom...", lambda: self._pick_custom_color(residue_spec))

        label_menu = menu.addMenu("Label")
        label_menu.addAction("Residue", lambda: self._run_commands(f"Context label:{residue_spec}", [f"label {residue_spec} residues"]))
        label_menu.addAction("Chain", lambda: self._run_commands(f"Context chain label:{chain_spec}", [f"label {chain_spec} residues"]))
        label_menu.addAction("Clear labels", lambda: self._run_commands("Context label clear", ["label delete"]))

        # Distance- and interaction-based neighbour selection.
        nearby_menu = menu.addMenu("Nearby")
        for cutoff in (3.5, 4.0, 5.0, 6.0, 8.0):
            nearby_menu.addAction(
                f"Residues within {cutoff:g} A",
                lambda checked=False, c=cutoff, rs=residue_spec: self._run_commands(
                    f"Context nearby {c}A:{rs}",
                    [f"select ({rs}) :<{c}", "view sel"],
                ),
            )
        nearby_menu.addSeparator()
        nearby_menu.addAction(
            "H-bond partners (residue)",
            lambda: self._run_commands(
                f"Context hbonds:{residue_spec}",
                [
                    f"hbonds ({residue_spec}) restrict any",
                    f"select ({residue_spec}) :< 4.5 & ~({residue_spec})",
                    "view sel",
                ],
            ),
        )
        nearby_menu.addAction(
            "Aromatic neighbours (pi-stacking proxy)",
            lambda: self._run_commands(
                f"Context aromatic neighbours:{residue_spec}",
                [
                    f"select (({residue_spec}) :<6) & :HIS,PHE,TYR,TRP & ~({residue_spec})",
                    "view sel",
                ],
            ),
        )
        nearby_menu.addAction(
            "Salt-bridge candidates (charged within 5 A)",
            lambda: self._run_commands(
                f"Context salt bridge:{residue_spec}",
                [
                    f"select (({residue_spec}) :<5) & :ASP,GLU,LYS,ARG,HIS & ~({residue_spec})",
                    "view sel",
                ],
            ),
        )
        nearby_menu.addAction(
            "Hydrophobic core neighbours (within 5 A)",
            lambda: self._run_commands(
                f"Context hydrophobic:{residue_spec}",
                [
                    f"select (({residue_spec}) :<5) & :ALA,VAL,LEU,ILE,MET,PHE,TRP,PRO,GLY & ~({residue_spec})",
                    "view sel",
                ],
            ),
        )
        nearby_menu.addAction(
            "Disulfide partners (CYS within 3 A of S)",
            lambda: self._run_commands(
                f"Context disulfide:{residue_spec}",
                [
                    f"select (({residue_spec}) :<3.0) & :CYS & ~({residue_spec})",
                    "view sel",
                ],
            ),
        )

        ai_menu = menu.addMenu("AI")
        ai_menu.addAction("Analyze residue", lambda: self._launch_ai_prompt(
            f"Analyze {residue_spec} with evidence and confidence.", "analyze"))
        ai_menu.addAction("List interactions in 5 A", lambda: self._launch_ai_prompt(
            f"List the residues within 5 A of {residue_spec} and classify each "
            f"interaction (H-bond, salt bridge, pi-stacking, hydrophobic, "
            f"disulfide). Cite distances.", "analyze"))
        ai_menu.addAction("Improve chain view", lambda: self._launch_ai_prompt(
            f"Improve the view for {chain_spec} and apply the changes directly.",
            "agent"))

        try:
            point = QPoint(*event.global_position())
        except Exception:
            point = self.session.ui.main_window.mapToGlobal(QPoint(x, y))
        self.session.ui.post_context_menu(menu, point)

    def _selected_residues_from_session(self):
        residues = []
        seen = set()

        def add_residue(residue):
            if residue is None:
                return
            key = str(getattr(residue, "atomspec", "") or id(residue))
            if key in seen:
                return
            seen.add(key)
            residues.append(residue)

        try:
            from chimerax.atomic import selected_residues

            for residue in selected_residues(self.session):
                add_residue(residue)
        except Exception:
            pass
        try:
            from chimerax.atomic import selected_atoms

            for atom in selected_atoms(self.session):
                add_residue(getattr(atom, "residue", None))
        except Exception:
            pass
        return residues

    # ------------------------------------------------------------------
    # Selection-context menu (empty-space right-click while something is
    # selected). Mirrors the per-residue menu but uses the special spec
    # "sel" that ChimeraX expands to the current selection.
    # ------------------------------------------------------------------
    def _show_selection_menu(self, event, sel_residues):
        from Qt.QtCore import QPoint
        from Qt.QtWidgets import QMenu

        n = len(sel_residues)
        chains = sorted({getattr(r, "chain_id", "?") for r in sel_residues})
        model_ids = sorted(
            {
                str(getattr(getattr(r, "structure", None), "id_string", "") or "")
                for r in sel_residues
            }
        )
        model_ids = [model_id for model_id in model_ids if model_id]
        model_spec = f"#{model_ids[0]}" if len(model_ids) == 1 else None
        title_text = f"Selection ({n} residue{'s' if n != 1 else ''}, chains {', '.join(chains)})"

        menu = QMenu(self.session.ui.main_window)
        title = menu.addAction(title_text)
        title.setEnabled(False)
        menu.addSeparator()

        action_menu = menu.addMenu("Action")
        action_menu.addAction("Focus", lambda: self._run_commands(
            "Context focus selection", ["view sel"]))
        action_menu.addAction("Invert selection", lambda: self._run_commands(
            "Context invert selection", ["select ~sel"]))
        action_menu.addAction("Clear selection", lambda: self._run_commands(
            "Context clear selection", ["~select"]))
        action_menu.addSeparator()
        action_menu.addAction("Delete selected atoms...", lambda: self._delete_target_atoms("sel", "current selection"))
        if model_spec:
            action_menu.addAction(
                "Delete waters / solvent in selected model...",
                lambda checked=False, ms=model_spec: self._run_solvent_action("delete", ms),
            )
        action_menu.addAction(
            "Delete waters / solvent in all models...",
            lambda checked=False: self._run_solvent_action("delete", None),
        )

        show_menu = menu.addMenu("Show")
        show_menu.addAction("Atoms", lambda: self._run_commands(
            "Context show atoms (sel)", ["show sel atoms"]))
        show_menu.addAction("Sticks", lambda: self._show_sticks("sel"))
        show_menu.addAction("Cartoon", lambda: self._run_commands(
            "Context show cartoon (sel)", ["show sel cartoons"]))
        show_menu.addAction("Surface", lambda: self._run_commands(
            "Context surface (sel)", ["surface sel"]))
        self._add_solvent_display_actions(show_menu, "show", model_spec)

        hide_menu = menu.addMenu("Hide")
        hide_menu.addAction("Atoms", lambda: self._run_commands(
            "Context hide atoms (sel)", ["hide sel atoms"]))
        hide_menu.addAction("Sticks", lambda: self._hide_sticks("sel", forget=True))
        hide_menu.addAction("Cartoon", lambda: self._run_commands(
            "Context hide cartoon (sel)", ["hide sel cartoons"]))
        hide_menu.addAction("Surface", lambda: self._run_commands(
            "Context hide surface (sel)", ["~surface sel"]))
        hide_menu.addAction("Everything", lambda: self._run_commands(
            "Context hide all (sel)",
            ["hide sel atoms", "hide sel cartoons", "~surface sel"]))
        self._add_solvent_display_actions(hide_menu, "hide", model_spec)

        color_menu = menu.addMenu("Color")
        color_menu.addAction("By element", lambda: self._run_commands(
            "Context color byelement (sel)", ["color sel byelement"]))
        color_menu.addAction("By chain", lambda: self._color_and_restore(
            "Context color bychain (sel)", "sel", "bychain"))
        preset_menu = color_menu.addMenu("Preset")
        for color_name in ("yellow", "cyan", "magenta", "hotpink",
                           "cornflowerblue", "orange", "gold"):
            preset_menu.addAction(
                color_name,
                lambda checked=False, c=color_name: self._color_and_restore(
                    f"Context color {c} (sel)", "sel", c))
        color_menu.addAction("Custom...",
                             lambda: self._pick_custom_color("sel"))

        label_menu = menu.addMenu("Label")
        label_menu.addAction("Selected residues",
                             lambda: self._run_commands(
                                 "Context label sel",
                                 ["label sel residues"]))
        label_menu.addAction("Clear labels",
                             lambda: self._run_commands(
                                 "Context label clear",
                                 ["label delete"]))

        ai_menu = menu.addMenu("AI")
        ai_menu.addAction("Analyze selection",
                          lambda: self._launch_ai_prompt(
                              "Analyze the current selection with "
                              "evidence and confidence.", "analyze"))

        try:
            point = QPoint(*event.global_position())
        except Exception:
            x, y = event.position()
            point = self.session.ui.main_window.mapToGlobal(QPoint(x, y))
        self.session.ui.post_context_menu(menu, point)

    def _run_commands(self, batch_label, commands):
        from chimerax.core.commands import run

        with command_batch(self.session, batch_label):
            for command in commands:
                run(self.session, command)
        self.session.logger.status(batch_label)

    def _add_solvent_display_actions(self, menu, action, model_spec=None):
        menu.addSeparator()
        if model_spec:
            menu.addAction(
                f"Waters / solvent (model {model_spec})",
                lambda checked=False, ms=model_spec, a=action: self._run_solvent_action(a, ms),
            )
        menu.addAction(
            "Waters / solvent (all models)",
            lambda checked=False, a=action: self._run_solvent_action(a, None),
        )

    def _confirm_destructive(self, title, message):
        try:
            from Qt.QtWidgets import QMessageBox

            reply = QMessageBox.question(
                self.session.ui.main_window,
                title,
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            return reply == QMessageBox.StandardButton.Yes
        except Exception:
            return True

    def _delete_target_atoms(self, spec, label):
        if not self._confirm_destructive(
            "Delete atoms",
            f"Delete atoms for {label}?\n\nThis removes them from the model, not just from the display.",
        ):
            return
        try:
            from .chain_cleanup import delete_atomspec

            message = delete_atomspec(self.session, spec)
        except Exception as err:
            message = str(err) if str(err) else err.__class__.__name__
            self.session.logger.status(message)
            self.session.logger.warning(message)
            return
        self.session.logger.status(message)

    def _keep_only_chain(self, chain_spec):
        if not self._confirm_destructive(
            "Keep only this chain",
            f"Keep {chain_spec} and delete every other chain in the same model?\n\nThis removes atoms from the model.",
        ):
            return
        try:
            from .chain_cleanup import keep_only_chain

            message = keep_only_chain(self.session, chain_spec)
        except Exception as err:
            message = str(err) if str(err) else err.__class__.__name__
            self.session.logger.status(message)
            self.session.logger.warning(message)
            return
        self.session.logger.status(message)

    def _run_solvent_action(self, action, model_spec=None):
        if action == "delete":
            scope = model_spec or "all models"
            if not self._confirm_destructive(
                "Delete waters / solvent",
                f"Delete waters / solvent in {scope}?\n\nThis removes atoms from the model, not just from the display.",
            ):
                return
        try:
            from .chain_cleanup import delete_solvent, hide_solvent, show_solvent

            functions = {
                "delete": delete_solvent,
                "hide": hide_solvent,
                "show": show_solvent,
            }
            message = functions[action](self.session, model_spec=model_spec)
        except Exception as err:
            message = str(err) if str(err) else err.__class__.__name__
            self.session.logger.status(message)
            self.session.logger.warning(message)
            return
        self.session.logger.status(message)

    def _color_and_restore(self, batch_label, spec, color):
        from chimerax.core.commands import run

        with command_batch(self.session, batch_label):
            run(self.session, f"color {spec} {color}")
            restore_charge_colors(self.session, spec)
        self.session.logger.status(batch_label)

    def _show_sticks(self, residue_spec):
        with command_batch(self.session, f"Context sticks:{residue_spec}"):
            show_sticks_with_cartoon_anchor(self.session, residue_spec)
        self.session.logger.status(f"Context sticks:{residue_spec}")

    def _hide_sticks(self, residue_spec, *, preserve_marked=False, forget=False):
        with command_batch(self.session, f"Context hide sticks:{residue_spec}"):
            hide_sticks_preserving_sidechains(
                self.session,
                residue_spec,
                preserve_marked=preserve_marked,
                forget=forget,
            )
        self.session.logger.status(f"Context hide sticks:{residue_spec}")

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
        self._color_and_restore(f"Context color custom:{spec}", spec, color.name())


def _apply_modifier_drag_bindings(session):
    """Bind drag-modifier shortcuts so the user gets predictable behavior:
      Shift + left-drag  → translate (pan)
      Ctrl  + left-drag  → ChimeraX built-in select (rectangle / pick, no rotate)
    Idempotent — safe to call repeatedly."""
    if not session.ui.is_gui:
        return
    mm = session.ui.mouse_modes

    translate_mode = mm.named_mode("translate")
    if translate_mode is not None:
        try:
            mm.bind_mouse_mode("left", ["shift"], translate_mode)
        except Exception:
            pass
        for action_name in ("two finger swipe", "three finger swipe"):
            try:
                mm.bind_mouse_mode(
                    trackpad_action=action_name,
                    trackpad_modifiers=["shift"],
                    mode=translate_mode,
                )
            except Exception:
                pass

    select_mode = None
    for candidate in ("select", "select region", "rectangle select", "pick"):
        try:
            select_mode = mm.named_mode(candidate)
        except Exception:
            select_mode = None
        if select_mode is not None:
            break
    if select_mode is not None:
        try:
            mm.bind_mouse_mode("left", ["control"], select_mode)
        except Exception:
            pass
        for action_name in ("two finger swipe", "three finger swipe"):
            try:
                mm.bind_mouse_mode(
                    trackpad_action=action_name,
                    trackpad_modifiers=["control"],
                    mode=select_mode,
                )
            except Exception:
                pass


def _mode_is_current(mode):
    return getattr(mode, "_codex_bridge_pick_mode_version", 0) == PICK_MODE_VERSION


def _installed_pick_modes_are_current(session):
    for attr in (
        "_codex_bridge_residue_pick_mode",
        "_codex_bridge_residue_add_pick_mode",
        "_codex_bridge_chain_pick_mode",
        "_codex_bridge_chain_add_pick_mode",
        "_codex_bridge_context_menu_mode",
    ):
        mode = getattr(session, attr, None)
        if mode is None or not _mode_is_current(mode):
            return False
    return True


def _remove_stale_codex_mouse_modes(mm):
    try:
        stale_modes = [
            mode
            for mode in list(getattr(mm, "_available_modes", []))
            if getattr(mode, "name", "") in _CODEX_MOUSE_MODE_NAMES
        ]
        if not stale_modes:
            return
        stale_ids = {id(mode) for mode in stale_modes}
        mm._available_modes = [
            mode
            for mode in getattr(mm, "_available_modes", [])
            if id(mode) not in stale_ids
        ]
        mm._bindings = [
            binding
            for binding in getattr(mm, "_bindings", [])
            if id(getattr(binding, "mode", None)) not in stale_ids
        ]
        if hasattr(mm, "_trackpad_bindings"):
            mm._trackpad_bindings = [
                binding
                for binding in getattr(mm, "_trackpad_bindings", [])
                if id(getattr(binding, "mode", None)) not in stale_ids
            ]
    except Exception:
        pass


def install_pick_modes(session):
    if not session.ui.is_gui:
        return

    _apply_modifier_drag_bindings(session)

    if _installed_pick_modes_are_current(session):
        try:
            session._codex_bridge_context_menu_mode.drag_delegate_mode_name = "zoom"
        except Exception:
            pass
        try:
            mm = session.ui.mouse_modes
            mm.bind_mouse_mode("right", [], session._codex_bridge_context_menu_mode)
            if getattr(session, "_codex_bridge_selection_click_mode", "residue") == "chain":
                mm.bind_mouse_mode("left", ["shift"], session._codex_bridge_chain_add_pick_mode)
            else:
                mm.bind_mouse_mode("left", ["shift"], session._codex_bridge_residue_add_pick_mode)
        except Exception:
            pass
        return

    mm = session.ui.mouse_modes
    _remove_stale_codex_mouse_modes(mm)
    try:
        mm.trackpad.enable_multitouch(True)
    except Exception:
        pass
    try:
        translate_mode = mm.named_mode("translate")
        if translate_mode is not None:
            for action_name in ("three finger swipe", "two finger swipe"):
                for modifiers in (["command"], ["shift", "command"]):
                    try:
                        mm.bind_mouse_mode(trackpad_action=action_name, trackpad_modifiers=modifiers, mode=translate_mode)
                    except Exception:
                        pass
    except Exception:
        pass
    residue_mode = CodexResiduePickMode(session)
    residue_add_mode = CodexResidueAddPickMode(session)
    chain_mode = CodexChainPickMode(session)
    chain_add_mode = CodexChainAddPickMode(session)
    menu_mode = CodexContextMenuMode(session)
    mm.add_mode(residue_mode)
    mm.add_mode(residue_add_mode)
    mm.add_mode(chain_mode)
    mm.add_mode(chain_add_mode)
    mm.add_mode(menu_mode)
    session._codex_bridge_residue_pick_mode = residue_mode
    session._codex_bridge_residue_add_pick_mode = residue_add_mode
    session._codex_bridge_chain_pick_mode = chain_mode
    session._codex_bridge_chain_add_pick_mode = chain_add_mode
    session._codex_bridge_context_menu_mode = menu_mode
    current_mode = getattr(session, "_codex_bridge_selection_click_mode", "residue") or "residue"
    if current_mode == "chain":
        mm.bind_mouse_mode("left", [], chain_mode)
        mm.bind_mouse_mode("left", ["shift"], chain_add_mode)
    elif current_mode == "residue" or not getattr(session, "_codex_bridge_pick_mode_initialized", False):
        mm.bind_mouse_mode("left", [], residue_mode)
        mm.bind_mouse_mode("left", ["shift"], residue_add_mode)
        session._codex_bridge_selection_click_mode = "residue"
    mm.bind_mouse_mode("right", [], menu_mode)
    session._codex_bridge_pick_mode_initialized = True


def bind_pick_mode(session, which):
    install_pick_modes(session)
    mm = getattr(getattr(session, "ui", None), "mouse_modes", None)
    if mm is None:
        # nogui mode — mouse pick modes are GUI-only, no-op silently
        return f"Pick mode '{which}' skipped (no GUI mouse_modes)."
    if which == "residue":
        mm.bind_mouse_mode("left", [], session._codex_bridge_residue_pick_mode)
        mm.bind_mouse_mode("left", ["shift"], session._codex_bridge_residue_add_pick_mode)
        session._codex_bridge_selection_click_mode = "residue"
        session._codex_bridge_last_chain_selection_spec = ""
        return "Left click picks residues; Shift-left click adds residues."
    if which == "chain":
        mm.bind_mouse_mode("left", [], session._codex_bridge_chain_pick_mode)
        mm.bind_mouse_mode("left", ["shift"], session._codex_bridge_chain_add_pick_mode)
        session._codex_bridge_selection_click_mode = "chain"
        return "Left click picks chains; Shift-left click adds chains."
    if which == "menu":
        mm.bind_mouse_mode("right", [], session._codex_bridge_context_menu_mode)
        return "Right click now opens the residue context menu."
    if which == "default":
        mm.bind_standard_mouse_modes(buttons=("left", "right"))
        session._codex_bridge_selection_click_mode = ""
        session._codex_bridge_last_chain_selection_spec = ""
        return "Mouse buttons restored to default rotate/translate."
    raise ValueError(f"Unknown pick mode: {which}")
