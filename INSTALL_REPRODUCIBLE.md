# Install and reproduce ChimeraXbridge 0.2.1

Install the wheel and restart ChimeraX to get the shared workspace defaults on a
new computer. Existing saved preferences are retained when upgrading.

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
   or pin [v0.2.1](https://github.com/JAEYOONSUNG/ChimeraXbridge/releases/tag/v0.2.1).
2. Download **[ChimeraX_CodexBridge-0.2.1-py3-none-any.whl](https://github.com/JAEYOONSUNG/ChimeraXbridge/releases/download/v0.2.1/ChimeraX_CodexBridge-0.2.1-py3-none-any.whl)** from **Assets**.
   You do not need Git or the **Source code** archives for a wheel installation.
3. In the ChimeraX command line, run:

   ```chimerax
   toolshed install "~/Downloads/ChimeraX_CodexBridge-0.2.1-py3-none-any.whl"
   ```

4. Restart ChimeraX. If installation was deferred because the old plugin was in
   use, restarting completes the update.

Installation is complete at this point. The sequence panel, tabbed sidebar,
muted nucleotide fills and transparent 300 DPI PNG defaults are ready on a fresh
installation. No additional setup command or AI login is required for local tools.

The quoted path must identify the wheel on your computer. `~` expands to your
home folder in the ChimeraX installer. Use a quoted absolute path if your
download folder is elsewhere, for example:

```chimerax
toolshed install "C:/Downloads/ChimeraX_CodexBridge-0.2.1-py3-none-any.whl"
```

Use the filename exactly as downloaded; if a browser adds `(1)`, choose the
original filename or rename the duplicate before installing. Do not use a
GitHub web-page URL as the local wheel path, and do not install the bundle into
system Python with `pip`.

## Defaults and first-run check

A fresh 0.2.1 installation uses these defaults automatically:

| Setting | Value |
| --- | --- |
| Icons / helper panels | Original toolbar icons / tabbed right panels |
| Sequence | Visible, All chains; existing 10-position guides |
| Sequence fill colors | Amino-acid charge off; nucleotides on, muted palette |
| Image export | PNG, 300 DPI, aspect lock on, transparent background on |
| Width / height | Use this computer's current graphics viewport |
| Bookmark capture | Camera, display, colors and lighting on; selection off |

Existing settings files retain their saved choices, including older color and
export defaults. The release does not replace your preferences on every launch.
To share a molecular scene and its bookmarks, share a ChimeraX `.cxs` session too;
the plugin installation does not transfer molecular data or another user's accounts.

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

Open `codex tool` and use the backend selector and **Setup** menu. Each user
connects their own account or API key. The plugin connects an existing account;
it does not purchase subscriptions, activate paid plans or change billing.

| Route | Where you finish setup | Who retains the credential |
| --- | --- | --- |
| Codex, Claude or Gemini account | The installed CLI's terminal/browser login, launched from Setup | That CLI, according to its own login settings |
| OpenAI API | The masked key dialog inside ChimeraX | ChimeraXbridge memory for this app session only |

Codex supports ChatGPT sign-in for subscription access and API-key sign-in for
usage-based access. OpenAI API keys use the OpenAI Platform's API billing instead
of included ChatGPT plan credits. [OpenAI authentication documentation](https://learn.chatgpt.com/docs/auth).

Use **Model** and **Reasoning** to choose options supported by your backend/account.
These commands inspect the current choices without hard-coded model names:

```chimerax
codex backend
codex model
codex effort
```

### Installed CLI backends

1. Install the desired Codex, Claude or Gemini CLI if it is not already present.
   ChimeraX must be able to find its executable.
2. Select the backend in the Assistant, then choose its **Setup** entry. On macOS
   this starts its login in Terminal. Follow the CLI/browser instructions using
   your own account. On other systems, Setup copies the login command for you
   to run in your own terminal.
3. Return to the Assistant and refresh engine status, then submit a request.

The login commands used by current supported CLIs are:

| CLI | Terminal command / interaction |
| --- | --- |
| Codex | `codex login`, then complete browser sign-in |
| Claude | `claude auth login`, then complete account sign-in |
| Gemini | `gemini`, then choose **Sign in with Google** in the interactive CLI |

These are terminal commands, not ChimeraX commands. If Terminal cannot be opened,
run the indicated command yourself. CLI authentication is not the session-only
OpenAI key described below; the CLI controls how its login is stored.
Engine status detects the installed executable; the CLI verifies login and account
access when an AI request runs.

If ChimeraX cannot locate an installed CLI, set its executable path **in your
shell before launching ChimeraX**:

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

1. Select **OpenAI API** in the Assistant and choose **Setup** to open
   **Connect OpenAI API**.
2. Enter your API key in the masked input. The dialog uses it only for this
   ChimeraX app session; it is not written to a settings file or shell environment,
   added to logs, or copied to the clipboard by the plugin.
3. Optionally click **Check connection**. This makes an explicit, background
   request to OpenAI's model-list endpoint; it does not send a molecular scene or
   run a model prompt. Opening the dialog does not contact the API. Checking a
   draft key does not save it or change the selected backend.
4. Click **Use key** to activate it and select the OpenAI API backend, then choose
   your model in the Assistant. A connection check is not required first.

Connection checking confirms that the key can access the model-list endpoint.
It does not guarantee that a selected model is permitted, that a later request
fits your limits, or that the account has usable credits. An error appears in
the dialog so you can correct the key or connection.

Use **Clear session key** to remove a key previously activated in this app.
Quitting ChimeraX also removes it, so you must enter it again after restarting.
The dialog never fills a configured key back into the input; it shows only that
a key is available. **Close** discards an unactivated draft. Clearing a session
key does not edit environment variables you configured outside the app; an
environment key, if present, becomes available again.

Advanced users can still provide `OPENAI_API_KEY` or `CODEX_BRIDGE_OPENAI_API_KEY`
in the launch environment instead of entering a key each app session. To select
the backend by command:

```chimerax
codex backend openai
codex tool
```

The direct OpenAI backend does not use a Codex CLI subscription login. Keep keys
out of repository files and shared examples, and choose the route you intend to use.

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
git clone --branch v0.2.1 https://github.com/JAEYOONSUNG/ChimeraXbridge.git
cd ChimeraXbridge
git rev-parse HEAD
```

Inside ChimeraX, use the **absolute path** to that clone:

```chimerax
devel install "/absolute/path/to/ChimeraXbridge"
```

Restart ChimeraX. The main command line is not a shell: use the actual clone path
instead of `$HOME` or shell command substitution. To work on the latest source
instead of a release, omit `--branch v0.2.1` when cloning.

## Updating and reinstalling

For normal upgrades, download the newer release wheel and run `toolshed install`
with its path, then restart ChimeraX. Keep the release tag/version with your
analysis notes when reproducibility matters.

For a source checkout on a branch, run `git pull --ff-only` in that checkout,
repeat `devel install` with its absolute path, and restart. A checkout pinned to
`v0.2.1` stays at that version until you deliberately switch tags/branches.

If reinstalling the **same version**, ChimeraX accepts:

```chimerax
toolshed install "~/Downloads/ChimeraX_CodexBridge-0.2.1-py3-none-any.whl" reinstall true
```

For a stubborn same-version repair, close ChimeraX, start it in safe mode
(`--safemode`, which avoids loading third-party bundles), run that reinstall
command, then restart normally. An in-use bundle can defer installation, so a
successful command in the old session does not mean its already-loaded Python
classes have changed.

### Optional: reset an existing UI to the shared defaults

Fresh installations need no profile command. If you deliberately want to replace
an existing installation's UI preferences with the shared defaults, this
compatibility command is still available inside ChimeraX:

```chimerax
codex profile jaeyoon
codex profile jaeyoon apply true
```

The first command shows the settings; only `apply true` changes them.
`codex profile` is a custom ChimeraXbridge settings command, not an OpenAI account
profile or an installation method. It changes the listed UI/export preferences
while preserving molecular coordinates, 3D colors, selection, camera, saved
bookmark data, executable paths, output directories and credentials.

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
