# ChimeraXbridge

**Version 0.2.3 · UCSF ChimeraX 1.10.x · tested on macOS with ChimeraX 1.10.1**

ChimeraXbridge adds a searchable sequence panel, compact molecular controls,
camera bookmarks, publication image export, six local Quick actions, and an
optional AI assistant inside ChimeraX.

<img src="docs/images/fervitase-main.png" width="100%" alt="ChimeraX with the ChimeraXbridge AI toolbar, sequence panel and chain controls, showing the Fervitase model with its Ca2+ and Na+ sites">

**The sequence panel, display controls, bookmarks, image export and Quick buttons work without an AI account.**
To give the AI assistant tasks in natural language, sign in to your chosen service
or configure your own API key separately. External analysis programs and personal
credentials are not included in the plugin.

## Installation: use the same plugin on another computer

1. Download **[ChimeraX_CodexBridge-0.2.3-py3-none-any.whl](https://github.com/JAEYOONSUNG/ChimeraXbridge/releases/download/v0.2.3/ChimeraX_CodexBridge-0.2.3-py3-none-any.whl)**
   from the [latest release](https://github.com/JAEYOONSUNG/ChimeraXbridge/releases/latest).
   Download the `.whl` file instead of GitHub's **Source code** archive to skip the build step.
2. Install it from the ChimeraX command line using the path to the downloaded file.

   ```chimerax
   toolshed install "~/Downloads/ChimeraX_CodexBridge-0.2.3-py3-none-any.whl"
   ```

   Adjust the path if you downloaded the file to a different folder. Put paths containing spaces in quotes.
3. Restart ChimeraX to finish installation. On a fresh installation, the sequence
   panel opens automatically, the right-side panels use tabs, DNA/RNA colors are
   muted, and image export defaults to transparent PNG at 300 DPI.

**Just install → restart.** No extra setup commands or Codex account are required.
Updates preserve your saved display settings.

[v0.2.3 release](https://github.com/JAEYOONSUNG/ChimeraXbridge/releases/tag/v0.2.3) ·
[Detailed installation, update and AI connection guide](INSTALL_REPRODUCIBLE.md)

The wheel contains the plugin code, toolbar icons, built-in help and workspace
defaults. Install it through **ChimeraX**, rather than system Python.
Local sequence, display, bookmark, export and Quick workflows need no AI login.
Geometric cavity analysis requires optional `pyKVFinder`; without it, the result
explains the fallback used and does not invent cavity measurements.

## What is new in 0.2.3

- **Cmd +** / **Cmd −** (**Ctrl** on Windows/Linux) zoom the whole ChimeraX
  interface from 50% to 250%, like browser zoom; **Cmd/Ctrl 0** resets and the
  level is remembered. `codex zoom 125` sets a level directly.
- Windows: AI command documentation is found from the running installation,
  text files and tool output are read as UTF-8 on non-UTF-8 locales, and
  detailed toolbar icons stay legible at 100–150% display scaling.

### Also in 0.2.2

- Display controls show the exact color target and label **Width**, **Thickness**,
  and the transparency scale. Slider and preset changes are identified as immediate.
- **Save contents**, **Save bookmark**, and **View size** explain the bookmark
  and image workflow. **Ask AI** and **Run command** distinguish the two inputs.
- Action Pad shows target names and an instruction for its row menus; the cavity
  empty state points to **AI → Quick → Cavity**.

- **Palette…** replaces the ambiguous **Pick** button. Colorful swatches,
  a screen eyedropper, **Copy HEX**, and **Apply color** separate choosing a
  color from changing the structure. The current application target stays visible.
- A fresh installation starts with the shared UI defaults after restarting;
  existing saved preferences are preserved when upgrading.
- **Setup** now accepts an OpenAI API key directly in a masked dialog, with
  optional connection checking and a key kept only for the current app session.
- CLI setup uses the supported Codex/Claude login commands and Gemini's
  interactive sign-in flow.

## Workspace features

Current source builds add **Models → Chains & molecules**: live per-chain
**Show** and **Select** checkboxes, chain search, **Only this**, **Show all chains**,
and a visible nonprotein molecule selector in the Models window. Atom, cartoon
and existing molecular-surface visibility update together, including changes
made elsewhere in ChimeraX. The original model/surface tree remains under
**Advanced models**. See the [chain controls guide](docs/model-chain-controls.md).

- The sequence panel opens at startup, with guides every 10 residues, muted
  nucleotide palettes, and a persistent search count, including overlapping motifs.
- Eight right-side panels scroll through their complete contents. Nested lists
  and evidence text scroll first, then the containing panel; scrolling over
  sliders and number fields does not accidentally change their values.
- The six **AI → Quick** buttons run local actions immediately, with background
  calculations, ranked candidates, cancellation, cached results and visual undo.
- **Quick Results** adds numeric sorting, filtering, an explicit current-preview
  indicator, and CSV/JSON/Markdown reports that retain every candidate.
- **Bookmarks** combines saved scene conditions, compact X/Y/Z controls, and
  PNG/JPEG/TIFF export with size, DPI, aspect lock and convenient size presets.
- **Display Controls → Palette…** provides colorful swatches, RGB/HEX editing,
  a screen eyedropper, **Copy HEX**, and a separate **Apply color** action.
- Collapsed Action Pad branches, candidate filters, **Hide All**, and zero
  transparency settings survive the relevant panel refreshes.

### Interface examples

<img src="docs/images/fervitase-toolbar.png" width="100%" alt="AI toolbar with Quick, Sequence, Modeling, Sites and Channels sections">

Screenshots are from ChimeraX 1.12 with an AlphaFold 3 model of Fervitase
(3 Ca²⁺, 1 Na⁺); residues with pLDDT below 50 are hidden.

<img src="docs/images/fervitase-sequence.png" width="100%" alt="Sequence panel finding the GTS motif at A375-377 with a persistent 1/1 match count">

| Chains and molecules | Action Pad targets | Image size, DPI and bookmarks |
| --- | --- | --- |
| <img src="docs/images/fervitase-models.png" width="280" alt="Models window listing Fervitase chain A and its Ca2+ and Na+ ions with Show and Select checkboxes"> | <img src="docs/images/fervitase-action-pad.png" width="280" alt="Action Pad with the metal-coordinating residues as the current selection"> | <img src="docs/images/fervitase-bookmarks.png" width="280" alt="Camera Bookmarks with PNG export at 300 DPI and transparent background"> |

## Examples you can follow

### 1. Inspect a structure without an AI account

Open a local PDB or mmCIF file through **File → Open**, then choose a structure in
**Models**, or select residues in the sequence/3D view. Click the **AI** toolbar tab:

| Quick button | What happens |
| --- | --- |
| **Analyze** | Reports composition, ligand/metal contacts, selection context and measured evidence. |
| **View** | Tidies cartoons, nucleic bases, ligands and ions while keeping existing colors and camera angle. |
| **Pocket** | Shows a ranked observed ligand neighborhood; tries geometric candidates when no usable ligand is present. |
| **Cavity** | Runs optional KVFinder geometry in the background and shows a translucent mesh with measured volume/depth. |
| **Figure** | Applies a clean white-background figure style, retains structure colors and offers image export. |
| **Zoom** | Focuses selected residues, otherwise an observed ligand/ion neighborhood, otherwise the model overview. |

The automatic target is selected atoms, then the highlighted model, then the
largest visible structure. **Quick Results** identifies the actual target and
reports the evidence and limitations. Repeating unchanged input reuses its result.

For example, click **Pocket**, sort candidates by **Contacts**, and type a ligand
name into the filter. Use the arrows or select a row to compare candidates. A
filter only changes the table; the preview label tells you which candidate is
actually displayed, even if its row is hidden by the filter. **Overlay** hides
generated surfaces/labels without hiding the structure.

**Save report → CSV / JSON / Markdown** saves all candidates, original ranks,
measurements, units, evidence and input provenance. Sorting and filtering never
discard report data. **Undo view** restores up to three recent visual steps.
**Run again** retains an explicitly chosen target; its menu also offers
**Recalculate without cache**.

Results are cached up to 64 MiB/eight entries. Structural edits, model moves,
renames, ID changes and closures mark affected results stale and hide invalid
previews. Cosmetic changes keep calculations reusable. Geometric candidates and
nearby residues are observations, not proof of binding affinity or catalysis.

### 2. Find a motif and use quieter nucleotide colors

```chimerax
codex seqbar
```

Choose a chain, or an all-chain/alignment view, then use the search field.
**Ctrl/Cmd+F** focuses search in the panel, the counter shows the current/total
matches, and **Esc** clears the focused query. Overlapping matches remain separate:
searching `AAA` within `AAAA` finds two occurrences. Guides mark groups of 10
residues or alignment columns.

Use the **DNA/RNA** arrow menu to choose **Muted bases**, **Purine / pyrimidine**,
or **Monochrome**. The main button toggles nucleotide fills; **Color key** explains
the selected palette. These sequence palettes do not recolor the 3D structure.

The sequence panel follows residue selection, shown side chains and aligned
structures. To export a structure-derived sequence alignment report:

```chimerax
codex structalign
```

### 3. Save the exact view, then export a figure

1. Open **Molecule Display → Bookmarks**. Use **X / Y / Z** for compact axis controls.
2. In **Save contents** beside **Save bookmark**, choose the conditions to remember: camera,
   pivot, clipping/model positions, display styles, colors/transparency,
   lighting/background, and optionally selection. Save the bookmark.
3. Save the ChimeraX session as `.cxs` to retain those bookmarks with the structure.
4. In the same panel's image controls, choose **PNG**, set **2400 × 1600 px** and
   **300 DPI**. Unlock the ratio first if the current view has a different ratio.
   The print-size hint reads approximately **20.3 × 13.5 cm**.
   Click **Export** and choose a destination.

**Quick Results → Export image** also opens and scrolls directly to these controls.
The **View size** button uses the current viewport size; its arrow offers **2× view**,
widths **1600 / 2400 / 3840 px**, and **Fit to 32 MP limit**. These presets preserve
the aspect ratio and DPI.

PNG and TIFF support transparent backgrounds; JPEG uses the current background.
DPI is written into the file metadata and is distinct from pixel dimensions.
The panel explains sizes beyond the 32 MP / 16,384 px-per-side limit before saving.

### 4. Keep controls usable in a small window

In **Display Controls → Compounds**, choose **All open structures** or one model
in **Models**, then click **Select**. The selection is replaced with nonpolymer
compounds, including ligands and ions; hidden atoms are included. Protein,
DNA/RNA and solvent are excluded by default. Choose **All nonprotein** to include
DNA/RNA and solvent. **Deselect** removes the target atoms while keeping other
selected objects, and ChimeraX Undo restores the previous selection. Molecular
coordination connections are included; distance annotations are not added.
Counts show selected atoms, residues and matching models. Closing a target model
requires an explicit new choice, even if its model number is reused.

Collapse **Compounds** with its arrow; the controls stay in the tab's scrollable
area. The mode and expansion state are saved, and the selected model is retained
when reopening the panel within the same ChimeraX session. With the action button
focused, **Space** toggles selection. See the
[ten-round improvement report](docs/compound-selection-improvements.md) for validation details.

To make the whole interface larger or smaller, press **Cmd +** / **Cmd −** on macOS
or **Ctrl +** / **Ctrl −** on Windows and Linux; **Cmd/Ctrl 0** returns to 100%.
Every ChimeraX window — toolbar, Log, Models, command line, menus, docked and
floating tools — scales from 50% to 250% like browser zoom. The 3D view is
unchanged, Help keeps its own page zoom, and the level is remembered. `codex zoom 125` sets a level directly.

**AI Assistant**, **Display Controls**, **Action Pad**, **Camera Bookmarks**,
**Quick Results**, **Models**, **CAVER**, and **Cavity Browser** all have full-panel
scrolling. Lists and evidence text retain their own scrolling, and you can reach
the bottom buttons without expanding the window.

In **Action Pad**, expand only the chains you need; collapsed branches and scroll
position are kept when the list refreshes. A/S/H/L/C menus apply actions, show,
hide, label and color to the indicated model, chain, residue or selection.
**Display Controls** provides color, transparency, cartoon/surface/stick controls,
named selections and rainbow palettes. Empty targets are explained next to the
controls, and valid hidden representations remain editable.

### 5. Choose, sample, copy or apply a color

1. Open **Molecule Display → Display Ctrl → Color → Palette…**. Choose a swatch
   from the colorful grid or adjust the hue/RGB/HEX controls.
2. To match a color already on your screen, click **Pick screen color**, then
   click the pixel you want. **Esc** cancels sampling.
3. Click **Copy HEX** to copy a value such as `#33AADD`, or check the **Apply to:**
   caption and click **Apply color** to color that scope and representation.

Choosing colors, sampling the screen and copying values in the palette do not
recolor the structure. Only **Apply color** does. The palette also works with
no model open: copying is available, while applying requires a valid target.
The palette stays open after applying so you can compare other colors.
**Whole models** colors entire models containing the selection and uses one
transparency value per model; the target caption states this before applying.
**Close** leaves the structure as it is, including any colors already applied.

For example, choose **Selection** and **Cartoon** in Display Controls to color
only the selected cartoon. Choose **All models** to color that representation
across all open models. The palette's target caption makes that choice visible
before you apply.


| Display targets and dimensions |
| --- |
| <img src="docs/images/fervitase-display-controls.png" width="360" alt="Display Controls applying color and transparency to the selected metal-site residues"> |

## Default workspace settings

These are the defaults for a fresh 0.2.3 installation. No profile command is
needed. Upgrades preserve saved choices, including older color/export defaults.

| Preference | Initial value |
| --- | --- |
| Toolbar / right helper panels | Original icons / tabbed layout |
| Sequence | Visible, All chains, guides every 10 positions |
| Sequence colors | Amino-acid charge fills off; nucleotide fills on, Muted bases |
| Image export | PNG, 300 DPI, aspect lock on, transparent background on |
| Initial export size | The recipient's current graphics viewport |
| Bookmark capture options | Camera, display, colors and lighting on; selection off |

Saved bookmarks, output directories, molecular data and AI credentials are
personal to each installation. Share a `.cxs` session separately when you also
want collaborators to open a particular molecular scene.

To deliberately reset an existing installation to these UI defaults later, the
compatibility command `codex profile jaeyoon apply true` remains available.
It is an optional ChimeraXbridge setting command, not an account or download step.
It leaves molecular coordinates, 3D colors, selection and camera intact.

## Optional AI assistant

To connect an AI service, open the panel with the commands below, then use **Setup**.
Choose account sign-in through an installed CLI or enter an OpenAI API key.
Manage subscriptions and billing directly with each service; OpenAI API usage is
billed separately from a ChatGPT subscription.

```chimerax
codex tool
codex backend
codex model
```

The AI Assistant's backend selector and **Setup** menu provide two connection routes:

- **Account/CLI login:** choose Codex, Claude or Gemini and use **Setup** to start
  that installed CLI's sign-in flow. Authentication finishes in its terminal/browser;
  the CLI manages its own saved login. Refresh engine status after signing in.
- **OpenAI API key:** open **Setup → Setup OpenAI API**. This works even before
  OpenAI is available in the backend selector. Enter your key in the masked
  field, then click **Use key** to select that backend. **Check connection** is optional;
  checking alone does not save or activate the key. **Clear session key** removes
  a key activated in this app. The plugin does not save it to disk, shell
  environment, logs or the clipboard; quitting ChimeraX removes it.

Codex's ChatGPT sign-in uses the account's subscription access. The direct OpenAI
API route uses separate API billing; ChatGPT plan credits do not cover API-key
usage. Setup does not purchase a subscription or change billing.
See [OpenAI's authentication documentation](https://learn.chatgpt.com/docs/auth).

Choose **Model** and **Reasoning** in the UI, or use `codex model` and `codex effort`;
available models depend on the selected service and account. `codex model default`
clears a model override. [Detailed connection steps](INSTALL_REPRODUCIBLE.md#optional-ai-connection)
explain key lifetime, checking, clearing and CLI setup.

| AI Assistant with Fervitase loaded | OpenAI API key setup |
| --- | --- |
| <img src="docs/images/fervitase-ai-assistant.png" width="320" alt="AI Assistant panel showing service, model, reasoning, mode and speed controls with the Fervitase sequence motifs"> | <img src="docs/images/ai-setup.png" width="430" alt="OpenAI connection dialog with an empty masked key field and separate check, use and clear controls"> |

The key field is empty; no real API key is shown.

After configuring an available backend, these are example **ChimeraX commands**:

```chimerax
codex context
ai Explain the ligand contacts around the currently selected residues
ai Align #2 to #1 using the catalytic core and explain the domain shift
codex ask Explain the evidence for a possible metal-binding site in the current selection
```

Replace example model IDs with your open structures. **Agent** mode may change
the scene; **Analyze/Chat** keep suggested commands separate by default. Inspect
`codex context` to see the session summary attached to requests. Depending on the
backend/workflow, context can include model names/IDs, sizes, selection, camera,
recent state changes, reference context and a viewport image.

The assistant also provides an in-app terminal: plain input runs ChimeraX
commands, and `!` prefixes shell commands. Inside that terminal:

```text
show sel
/sequence
/motif
/figure clean
/setup
```

Use **Enter** to submit an assistant request and **Shift+Enter** for a newline.
A **Stop** button appears while a request runs; it ends the request at its next
checkpoint and kills the backend CLI process immediately, so the prompt is never
locked behind a slow service. Closing the panel does the same.

Commands that an AI reply asks ChimeraX to run pass through one safety gate
before execution, on every path (agent action batches, commands embedded in an
analysis reply, and OpenAI tool calls). Commands that run scripts or code
(`runscript`, `pyscript`, `open` of `.py`/`.cxc` files, shell escapes),
install software (`toolshed`, `pip`), redefine commands (`alias`) or quit the
application are rejected and reported in the transcript. Commands you type in
the in-app terminal are yours and are not filtered.

Transient OpenAI API failures (rate limits, server errors, network drops) are
retried with backoff. Optional environment variables:
`CODEX_BRIDGE_BACKEND_TIMEOUT` (seconds per backend call, default 300),
`CODEX_BRIDGE_OPENAI_ATTEMPTS` (default 3) and `CODEX_BRIDGE_USER_DIR`
(where natural-language aliases and event logs are stored; the default follows
ChimeraX's per-user data directory on each platform).
`codex auto false` disables natural-language fallback in the main ChimeraX command
line. `codex routing` describes the routing modes; `codex selftest` reports local
integration and backend availability. Missing AI credentials do not disable the
local Quick or sequence/display/export tools.

## Additional tools

These launchers extend the local workflows; external services and executables
have their own installation, account and network requirements.

| Area | Available workflows |
| --- | --- |
| Sequence and conservation | BLAST, UniProt search, HHpred, SignalP, ConSurf-lite, 3D conservation comparison, hydrophobicity/MLP coloring and structural MSA reports. |
| Sites and interfaces | Catalytic-residue triage, existing/virtual metal coordination, PISA-style buried interface area, pockets and cavity overlays. |
| Modeling and docking | AlphaFold/AF Complex launchers; local Boltz; RAPiDock native/Docker/HPEPDOCK routes; HPEPDOCK; NucDock through HDOCK. |
| Tunnels and membranes | CAVER preparation/result import/tunnel display, lining-residue selection, virtual membrane slabs and hydrophobicity views. |
| Structure search | Foldseek, FoldMason, FoldDisco, DALI, VAST, PDBeFold/SSM and US-align launchers. |
| Molecular dynamics | Optional OpenMM-based setup when OpenMM is installed in ChimeraX's Python environment. |

The bundle does not install large scientific stacks automatically. **Setup** and
the terminal's `/setup` explain available setup routes. See
[optional dependency notes](INSTALL_REPRODUCIBLE.md#optional-scientific-tools) and
[RAPiDock setup](RAPIDOCK_SETUP.md).

## Install from source and contribute

For the exact release source:

```bash
git clone https://github.com/JAEYOONSUNG/ChimeraXbridge.git
cd ChimeraXbridge
```

Then run this **inside ChimeraX**, replacing the path with the absolute clone path:

```chimerax
devel install "/absolute/path/to/ChimeraXbridge"
```

Restart after installation. Record the checkout commit with `git rev-parse HEAD`
to reproduce a source build. [The install guide](INSTALL_REPRODUCIBLE.md) covers
updates, same-version reinstalls and reproducibility checks.

Run checks without opening or activating a desktop window:

```bash
python3 scripts/run_quality_check.py scroll
python3 scripts/run_quality_check.py panels
python3 scripts/run_quality_check.py export
python3 scripts/run_quality_check.py sequence
python3 scripts/run_quality_check.py quick
python3 scripts/check_release_ready.py
```

The quality runner forces ChimeraX `--nogui` with offscreen Qt and covers actual
widgets, supplied-pixel encoder output, state restoration and background work.
It does not recheck the native OpenGL renderer. Older visible GUI runners are
disabled unless `CODEX_ALLOW_VISIBLE_GUI_TESTS=1` is explicitly enabled after
desktop interaction is authorized.
Palette checks run offscreen and do not validate actual desktop screen sampling.

Plugin license: [MIT](license.txt). UCSF ChimeraX and optional services/tools are
distributed under their own terms.
