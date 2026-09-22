# Compound selection: ten improvement rounds

The Compounds section in Display Controls was reviewed and improved in ten
sequential rounds. Each round ran the real ChimeraX atomic/Qt regression suite
after its changes; failed checks were corrected before recording that round as
complete. All UI checks use offscreen Qt without opening a desktop window.

| Round | Improvement | Evidence checked |
| --- | --- | --- |
| 1 | Recover from target-discovery and selection errors with persistent inline feedback. | Injected failures preserve selection; refreshing retains action errors; retry restores normal operation. |
| 2 | Prevent reentrant selection and resolve surviving atoms after callbacks. | A nested callback executes one native action; closing a target during the callback does not leave stale atoms. |
| 3 | Include internal molecular coordination connections with selected atoms. | Internal pseudobond selection, boundary exclusion and native Undo/Redo. |
| 4 | Describe the actual action, scope and matching models. | Mode-specific accessible names, selected/partial counts, bounded model-name tooltips and explicit hidden-atom inclusion. |
| 5 | Limit pseudobonds to native molecular connection groups. | Actual ChimeraX metal-coordination/missing-structure constants; distance annotations stay outside automatic selection. A fixture using a display label instead of the native group name was corrected in this round. |
| 6 | Allow selection across all structures or within one chosen model. | Changing model scope does not change selection; each model target contains exactly its expected atoms. |
| 7 | Keep target identity stable through model lifecycle changes. | Rename updates the selector; a closed model stays unavailable even after its number is reused. |
| 8 | Remember the control state when reopening the panel. | Type/expansion preferences and the current session's model target are restored without changing selection. |
| 9 | Reduce repeated target-discovery work. | Residue-first filtering matches an independent atom-first reference; collapsed controls do not scan atoms; immediate feedback reuses resolved targets. |
| 10 | Protect scope changes during callbacks and improve compact keyboard operation. | An in-flight scope change aborts selection; Space toggles the action; a 300-pixel minimum prevents clipped controls, including at 14-point size. |

The performance fixture includes 12,005 atoms, connected modified amino acids,
a free amino acid and heavy water. It verifies exact target equality before
printing median discovery timings. This measures target discovery on synthetic
data, not overall ChimeraX rendering performance.

Run the accumulated regression suite with:

```bash
python3 scripts/run_quality_check.py compound_selection
```

The suite also checks empty scenes, hidden compounds, atom deletion, model
closure, selection Undo/Redo, cancellation of pending display edits, scrolling,
and read-only refresh. Existing display and scrolling suites are checked during
final integration. The installed package and local wheel are verified against
the complete working source before delivery.

Use **Display Controls → Compounds → Models** to choose the model scope, then
choose **Compounds** or **All nonprotein**. **Select** replaces the current
selection; **Deselect** removes the target atoms. Both modes include hidden atoms.
Mode and expansion preferences persist; a model target is retained only within
the same ChimeraX session. Closing a model requires a new explicit target choice.
