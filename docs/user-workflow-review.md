# User workflow review with Claude Opus 5.5

Codex and Claude Opus 5.5 reviewed the Models chain and molecule controls together.
The requested Claude model was used without substitution. Claude independently
examined screenshots and source, exercised real local crystal structures and a
protein/DNA complex in isolated ChimeraX sessions, and implemented the solvent
and deleted-atom Undo fixes. Codex reproduced additional interaction defects,
implemented the UI fixes and rechecked the integrated changes.

All interaction probes used real ChimeraX 1.10.1 atomic objects, surfaces and Qt
widgets, including `QTest` mouse and keyboard events. Qt ran offscreen; these
checks did not operate the user's live research scene or claim full OpenGL
pixel-rendering verification. Private molecular files and probe paths are not
included in the repository. Committed regressions use synthetic fixtures.

| Confirmed user problem | Implemented correction |
| --- | --- |
| ADP sharing a protein chain ID was absent from search results. | Search includes residue names; mixed chains identify their ligands in the row. |
| Space on a highlighted chain name did nothing. | Space toggles Show; Shift+Space toggles selection; Space on a Select cell keeps its native meaning. |
| Only this could act on a highlighted child hidden beneath a collapsed model. | Collapsed and filtered descendants are excluded from the action target. |
| UI reload discarded saved display styles and disconnected their Undo state. | The live controller is adopted by the replacement page, retaining its identity, memory and Undo/Redo references. |
| Hidden crystal waters made normal chains look partially shown and appeared during Show or Only this. | Mixed-chain visibility and fallback styles exclude solvent; existing solvent choices are restored exactly. Solvent-only rows remain explicitly controllable. |
| Undo after atom deletion or model closure logged a ChimeraX bug. | Native undo entries explicitly skip deleted Cython atoms and residues, restoring surviving objects. |
| Clicking the centre or right side of a Show/Select cell appeared to do nothing. | The entire cell responds to press/release, with one toggle per click. |
| Rotation, coordinate changes and selection caused unnecessary full surface scans. | Coordinate-only events skip the visibility pass; selection has a separate lightweight update path. Actual display changes still refresh visibility. |
| Logged color, transparency and lighting commands also caused full visibility scans. | The command hook compares a small surface-state fingerprint; appearance-only changes avoid full scans while real surface-mask changes still refresh the table. |

The Models molecule action is labeled **Select only** to distinguish replacement
selection from additive chain **Select** checkboxes. Both selection paths include
internal molecular coordination connections and exclude distance annotations.

The molecule section remains directly visible by default, following the user's
request to make it easy to find; it can be collapsed manually. The chain order
from the molecular structure is retained.

## Reproducible checks

```bash
python3 scripts/run_quality_check.py user_workflows
python3 scripts/run_quality_check.py model_chains
```

`check_user_workflows.py` drives the actual checkbox cells at their left edge,
centre and right edge. It checks keyboard selection, mixed-chain ligand search,
collapsed-row behavior, full module reload and Undo/Redo. A visibility-read spy
verifies that repeated model turns, coordinate edits and selections do not scan
surfaces. Logged appearance commands are checked too; an external hide command
is the positive control for a working update
path. `check_chain_visibility.py` records native `logger.bug` calls during
delete/close and Undo, rather than assuming a returned Undo call succeeded.

Existing model-panel, compound-selection and scrolling regressions are also
run during integration, along with source/wheel/local-installation consistency.

Claude's final cross-check found no blocking issue. The appearance follow-up
also verified logged color/rainbow/transparency/lighting/style commands on real
structures with SES surfaces, positive surface visibility controls, and changes
to surface targets with equal atom counts. Ordinary model-list updates, such as
creating label models, still refresh the panel; no claim is made that every
possible command is free of UI work.
