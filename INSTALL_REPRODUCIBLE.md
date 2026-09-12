# Install and reproduce ChimeraXbridge 0.2.0

Use this guide to install the published plugin on another computer and share a
workspace configuration. Copying another user's installed package directory or
AI account files is unnecessary.

## Requirements

- UCSF ChimeraX 1.10.x; release checks run on macOS with ChimeraX 1.10.1.
- The release wheel, or a clone of the matching release tag.
- For AI requests only: your own supported CLI login or API key.

The sequence panel, display controls, bookmarks, image export and local Quick
actions work without an AI account. Some scientific analyses need optional
dependencies; see [Optional scientific tools](#optional-scientific-tools).
The wheel is Python-only, but this release has not been validated on every OS or
every ChimeraX version. ChimeraX dependency ranges are declared in
[`bundle_info.xml`](bundle_info.xml).

## Recommended: install the release wheel

1. Open [the latest release](https://github.com/JAEYOONSUNG/ChimeraXbridge/releases/latest),
   or pin [v0.2.0](https://github.com/JAEYOONSUNG/ChimeraXbridge/releases/tag/v0.2.0).
2. Download **[ChimeraX_CodexBridge-0.2.0-py3-none-any.whl](https://github.com/JAEYOONSUNG/ChimeraXbridge/releases/download/v0.2.0/ChimeraX_CodexBridge-0.2.0-py3-none-any.whl)** from **Assets**.
   You do not need Git or the **Source code** archives for a wheel installation.
3. In the ChimeraX command line, run:

   ```chimerax
   toolshed install "~/Downloads/ChimeraX_CodexBridge-0.2.0-py3-none-any.whl"
   ```

4. Restart ChimeraX. If installation was deferred because the old plugin was in
   use, restarting completes the update.

Installation is complete at this point. The plugin's local tools work without
running `codex profile`, signing in to Codex, or downloading any account profile.

The quoted path must identify the wheel on your computer. `~` expands to your
home folder in the ChimeraX installer. Use a quoted absolute path if your
download folder is elsewhere, for example:

```chimerax
toolshed install "C:/Downloads/ChimeraX_CodexBridge-0.2.0-py3-none-any.whl"
```

Use the filename exactly as downloaded; if a browser adds `(1)`, choose the
original filename or rename the duplicate before installing. Do not use a
GitHub web-page URL as the local wheel path, and do not install the bundle into
system Python with `pip`.

## Optional: apply the shared UI preset

`codex profile` is a custom **ChimeraXbridge setting command**, not an OpenAI
Codex account profile or an installation method. The preset is already included
in the wheel. Skip this section to keep your current UI preferences.

To match JaeYoon's panel, color and image-export preferences, inspect and
explicitly apply the bundled preset inside ChimeraX:

```chimerax
codex profile
codex profile jaeyoon apply true
```

Inspection alone does not change settings. Installation does not automatically
apply the profile. The **jaeyoon** profile sets:

| Setting | Value |
| --- | --- |
| Icons / helper panels | Original toolbar icons / tabbed right panels |
| Sequence | Visible, All chains; existing 10-position guides |
| Sequence fill colors | Amino-acid charge off; nucleotides on, muted palette |
| Image export | PNG, 300 DPI, aspect lock on, transparent background on |
| Width / height | Reset to use this computer's current graphics viewport |
| Bookmark capture | Camera, display, colors and lighting on; selection off |

The profile leaves molecular coordinates, 3D colors, selection and camera intact.
It does not transfer login credentials, API keys, executable paths, output
directories, open structures or saved bookmark data. To share a particular
molecular scene and its bookmarks, save and share a ChimeraX `.cxs` session too.

Expected local UI behavior:

- The **AI** and **Molecule Display** toolbar tabs have icons.
- The sequence panel opens at startup; `codex seqbar` reopens a deliberately hidden panel.
- Guides group sequences in tens; the DNA/RNA menu includes muted and monochrome palettes.
- Right-side panel contents and bottom actions remain reachable by scrolling.
- With a structure open, **AI → Quick → Analyze / View / Pocket / Cavity / Figure / Zoom**
  opens a report or performs the corresponding local action.
- **Molecule Display → Bookmarks** exposes PNG/JPEG/TIFF export with size and DPI.

For installation and backend diagnostics:

```chimerax
codex selftest
codex backend
```

An unavailable backend is expected if you have not configured AI yet. It is not
a failure of the local UI. Without optional KVFinder, Cavity reports that geometry
was unavailable and clearly labels any fallback evidence.

## Optional AI connection

Open `codex tool` and use the backend selector and **Setup** menu. Each user signs
in to their own service. Use the **Model** and **Reasoning** controls to select
options supported by that backend/account. These commands inspect the current
choices without requiring a hard-coded model name:

```chimerax
codex backend
codex model
codex effort
```

### Installed CLI backends

For Codex, Claude or Gemini, install the desired CLI and complete its own login
flow. ChimeraX must be able to find that executable. The Setup menu provides
connection guidance. If needed, set an explicit executable path **in your shell
before launching ChimeraX**:

```bash
export CODEX_BRIDGE_CLI="/absolute/path/to/codex"
export CODEX_BRIDGE_CLAUDE_CLI="/absolute/path/to/claude"
export CODEX_BRIDGE_GEMINI_CLI="/absolute/path/to/gemini"
/Applications/ChimeraX-1.10.1.app/Contents/bin/ChimeraX
```

Set only the variables for installed backends. App launch environments may differ
from your terminal, especially on macOS. Inside ChimeraX, choose your installed backend:

```chimerax
codex backend codex
codex tool
```

Substitute `claude` or `gemini` for the other CLI backends.

### OpenAI API backend

Configure your own `OPENAI_API_KEY` or `CODEX_BRIDGE_OPENAI_API_KEY` in the launch
environment, or use the Assistant's API-key setup flow. Then run:

```chimerax
codex backend openai
codex tool
```

Keep keys out of repository files and shared shell examples. A configured CLI
login and an API key are separate connections; choose the backend you intend to use.

### Try a request

With a structure open and a backend available:

```chimerax
codex context
ai 현재 선택한 잔기와 주변 리간드의 접촉을 설명해줘
```

`codex context` shows the session summary attached to requests. Depending on the
workflow, AI requests may also include reference information and a viewport
image. Use **Agent** for scene-changing requests or **Analyze/Chat** to review
explanations and suggested commands. `codex auto false` disables fallback from
unrecognized main-command-line text to natural language.

## Optional scientific tools

These are installed separately; they are not bundled just because a launcher is
present. Some routes use external web services instead of a local executable.
The **Setup** menu and `/setup` in the Assistant terminal show available guidance.

| Tool/workflow | Local setup or behavior |
| --- | --- |
| KVFinder cavity geometry | Requires `pyKVFinder` importable from ChimeraX's Python environment. Without it, results report the missing geometry and use available ligand/neighborhood evidence. |
| CAVER tunnels | Requires Java (`java` or `CAVER_JAVA`) and CAVER; `CAVER_HOME` / `CAVER_JAR` can locate a separate installation. Existing CAVER results can also be imported. |
| OpenMM dynamics | Requires `openmm` in ChimeraX's Python environment. |
| SignalP | `SIGNALP_EXE` / `SIGNALP6_EXE`, optionally `SIGNALP_MODEL_DIR`; available web/heuristic routes are labeled separately. |
| RAPiDock | Separate checkout (`RAPIDOCK_REPO`) and engine choice `RAPIDOCK_ENGINE`; engines include auto, native, docker and hpepdock. Docker is needed for its Docker route. |
| Boltz | Separate local executable/environment, optionally `BOLTZ_EXE`. |
| HPEPDOCK/HDOCK | External docking services; HPEPDOCK can use `HPEPDOCK_EMAIL`. |
| FoldMason/FoldDisco/US-align | Separate executables for local routes; launchers describe applicable external/native alternatives. |

To enable the same local cavity geometry used in this release's macOS checks,
run this **inside ChimeraX**, then restart:

```chimerax
pip install "pyKVFinder==0.8.3"
```

This is ChimeraX's `pip` command, which installs into its own Python environment.
Version 0.8.3 was tested with ChimeraX 1.10.1 on macOS; package availability and
build requirements can differ on other platforms. It needs no AI account.
This optional dependency is not installed automatically by ChimeraXbridge.

The terminal can guide optional RAPiDock/Boltz setup after confirmation:

```text
/setup
/setup rapidock --gpu auto
/setup boltz
```

These environments can require substantial downloads and are not necessary for
sequence/display/export workflows. See [RAPiDock setup](RAPIDOCK_SETUP.md).

## Install pinned source instead

In a shell:

```bash
git clone --branch v0.2.0 https://github.com/JAEYOONSUNG/ChimeraXbridge.git
cd ChimeraXbridge
git rev-parse HEAD
```

Inside ChimeraX, use the **absolute path** to that clone:

```chimerax
devel install "/absolute/path/to/ChimeraXbridge"
```

Restart ChimeraX. The main command line is not a shell: use the actual clone path
instead of `$HOME` or shell command substitution. To work on the latest source
instead of a release, omit `--branch v0.2.0` when cloning.

## Updating and reinstalling

For normal upgrades, download the newer release wheel and run `toolshed install`
with its path, then restart ChimeraX. Keep the release tag/version with your
analysis notes when reproducibility matters.

For a source checkout on a branch, run `git pull --ff-only` in that checkout,
repeat `devel install` with its absolute path, and restart. A checkout pinned to
`v0.2.0` stays at that version until you deliberately switch tags/branches.

If reinstalling the **same version**, ChimeraX accepts:

```chimerax
toolshed install "~/Downloads/ChimeraX_CodexBridge-0.2.0-py3-none-any.whl" reinstall true
```

For a stubborn same-version repair, close ChimeraX, start it in safe mode
(`--safemode`, which avoids loading third-party bundles), run that reinstall
command, then restart normally. An in-use bundle can defer installation, so a
successful command in the old session does not mean its already-loaded Python
classes have changed.

## Maintainer checks without desktop interruption

From the repository root:

```bash
python3 scripts/run_quality_check.py scroll
python3 scripts/run_quality_check.py panels
python3 scripts/run_quality_check.py export
python3 scripts/run_quality_check.py sequence
python3 scripts/run_quality_check.py quick
python3 scripts/check_release_ready.py
```

If ChimeraX is installed elsewhere, set `CHIMERAX_BIN` to its executable path in
the shell before running the quality checks. The default is the macOS 1.10.1 app.

Quality checks force ChimeraX `--nogui` and offscreen Qt. They exercise real
widgets and exported pixels without opening or activating a desktop window.
They do not replace a separate native OpenGL rendering check. Older visible GUI
runners are disabled unless desktop interaction is explicitly authorized and
`CODEX_ALLOW_VISIBLE_GUI_TESTS=1` is set.

Release source, package data, icons, documentation and the wheel must match the
published version. `scripts/check_release_ready.py` checks repository/package
readiness. Maintainers with an installed copy can additionally compare it with
the source/wheel using `python3 scripts/check_quality_payload.py`.

Machine-specific runtime probes, generated `scripts/*.cxc` launchers and account
files stay local. Publish the release wheel and matching source tag so another
user can install the same build directly.
