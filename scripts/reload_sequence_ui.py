"""Reload the sequence header and toolbar spacing without touching scene data."""
import importlib
from Qt.QtCore import QTimer


def reload_sequence_ui():
    from chimerax.codex_bridge import runtime_patches, sequence_bar, sequence_colors, sequence_color_view
    old_tools = [tool for tool in session.tools.list() if tool.tool_name == "Sequence Bar"]
    old = next((tool for tool in old_tools if tool.displayed()), old_tools[0] if old_tools else None)
    entry = getattr(old, "_current_entry", None) or {}
    mode = getattr(old, "_selection_click_mode", "residue")
    query = old.search_edit.text() if old is not None else ""
    spec = entry.get("spec")
    all_chains = getattr(old, "_all_chains_enabled", True)
    scroll_fractions = {}
    if old is not None:
        for name in ("sequence_text", "alignment_text", "all_chains_text"):
            scroll = getattr(old, name).horizontalScrollBar()
            scroll_fractions[name] = scroll.value() / max(1, scroll.maximum())
    for tool in old_tools:
        # Queued callbacks from versions without the closed-widget guard
        # must not repaint an old sequence panel after it is detached.
        tool.refresh = lambda: None
        tool._refresh_selection_state = lambda: None
        tool.delete()
    importlib.invalidate_caches()
    importlib.reload(runtime_patches)
    importlib.reload(sequence_colors)
    importlib.reload(sequence_color_view)
    importlib.reload(sequence_bar)
    runtime_patches._patch_tabbedtoolbar_section()
    runtime_patches.style_ai_toolbar(session)
    bar = sequence_bar.CodexSequenceBar.get_singleton(session)
    bar.all_chains_button.setChecked(all_chains)
    if spec is not None:
        index = bar.chain_combo.findData(spec)
        if index >= 0:
            bar.chain_combo.setCurrentIndex(index)
    # Preserve the existing mouse binding; only restore the displayed mode.
    bar._selection_click_mode = mode
    bar.selection_button.setText("Select: " + {"chain": "Chain", "atom": "Atom"}.get(mode, "Residue"))
    bar._populate_selection_menu(bar.selection_button.menu())
    bar.search_edit.setText(query)
    bar.display(True)

    def restore_scroll():
        if bar._closed:
            return
        for name, fraction in scroll_fractions.items():
            scroll = getattr(bar, name).horizontalScrollBar()
            scroll.setValue(round(fraction * scroll.maximum()))
    QTimer.singleShot(100, restore_scroll)
    session.logger.status("Sequence colors updated: AA charge and DNA/RNA fills with structure-color outlines.")


QTimer.singleShot(0, reload_sequence_ui)
