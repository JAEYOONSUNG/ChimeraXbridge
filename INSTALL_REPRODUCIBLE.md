# Reproducible ChimeraXbridge Setup

This document is the install path to give another user when they need the same
ChimeraXbridge environment from GitHub.

## Target Environment

- UCSF ChimeraX 1.10.x, tested locally with ChimeraX 1.10.1 on macOS.
- This repository cloned from GitHub.
- One AI backend:
  - Codex CLI installed and logged in, or
  - `OPENAI_API_KEY` / `CODEX_BRIDGE_OPENAI_API_KEY` set for the OpenAI API
    backend, or
  - Claude/Gemini CLI installed for those optional backends.

The bundle declares ChimeraX dependencies in `bundle_info.xml` and expects the
ChimeraX 1.10 package family.

## Before Publishing From This Machine

Run this from the repository root:

```bash
git status --short
python3 scripts/check_release_ready.py
```

Do not publish a release until:

- all release files under `src/`, `src/icons/`, top-level `icons/`, and
  user-facing docs are tracked and committed;
- `scripts/*.cxc` and `.codex_runtime_verify.json` remain local-only;
- toolbar icons referenced from `bundle_info.xml` exist under `src/icons/`;
- any wheel under `dist/` has been rebuilt from the same commit, or users are
  told to use `devel install` instead of the wheel.

## Install From GitHub

```bash
git clone https://github.com/JAEYOONSUNG/ChimeraXbridge.git
cd ChimeraXbridge
git rev-parse --short HEAD
```

Open ChimeraX and run:

```chimerax
devel install /absolute/path/to/ChimeraXbridge
```

On macOS this is usually:

```chimerax
devel install $HOME/ChimeraXbridge
```

Then restart ChimeraX. Do not copy files manually from another user's
`~/Library/Application Support/ChimeraX/...` directory; that creates a local
state that GitHub users cannot reproduce.

## Shell Install Alternative

```bash
/Applications/ChimeraX-1.10.1.app/Contents/bin/ChimeraX \
  --nogui \
  --cmd "devel install /absolute/path/to/ChimeraXbridge ; exit"
```

Restart ChimeraX after the command completes.

## Backend Setup

### Codex CLI

Install and log in to Codex CLI. If ChimeraX cannot find it from its launch
environment, launch ChimeraX with the full path:

```bash
export CODEX_BRIDGE_CLI="$(which codex)"
/Applications/ChimeraX-1.10.1.app/Contents/bin/ChimeraX
```

Optional model overrides:

```bash
export CODEX_BRIDGE_CODEX_FAST_MODEL=gpt-5.5
export CODEX_BRIDGE_CODEX_PRECISE_MODEL=gpt-5.5
```

### OpenAI API Backend

```bash
export OPENAI_API_KEY="sk-..."
/Applications/ChimeraX-1.10.1.app/Contents/bin/ChimeraX
```

Inside ChimeraX:

```chimerax
codex backend openai
```

Optional model overrides:

```bash
export CODEX_BRIDGE_OPENAI_FAST_MODEL=gpt-5.5
export CODEX_BRIDGE_OPENAI_PRECISE_MODEL=gpt-5.5
```

### Claude and Gemini

If using CLI backends, install the matching CLI and make sure ChimeraX can see
it in `PATH`, or set:

```bash
export CODEX_BRIDGE_CLAUDE_CLI=/absolute/path/to/claude
export CODEX_BRIDGE_GEMINI_CLI=/absolute/path/to/gemini
```

## Verify Inside ChimeraX

After restart:

```chimerax
codex selftest
codex tool
codex actions
codex seqbar
```

Expected result:

- AI toolbar icons render without "Unable to find icon" warnings.
- `Molecule Display > Sequence` opens the sequence panel.
- `Molecule Display > Action Pad` opens model/chain controls.
- The backend selector shows at least one available backend.

## Optional External Tools

These are not required for the core ChimeraX UI, sequence panel, model controls,
or OpenAI/Codex chat features.

- SignalP:
  - `SIGNALP_EXE` or `SIGNALP6_EXE`
  - optional `SIGNALP_MODEL_DIR`
  - without local SignalP, the plugin falls back to web or heuristic behavior.
- CAVER:
  - Java available as `java`, or set `CAVER_JAVA`;
  - optional `CAVER_HOME` and `CAVER_JAR`.
- RAPiDock:
  - `RAPIDOCK_REPO` for a local checkout;
  - `RAPIDOCK_ENGINE=auto|native|docker|hpepdock`;
  - Docker Desktop for the Docker engine;
  - `RAPIDOCK_ALLOW_EMULATION=1` only if enabling x86 emulation on macOS.
- HPEPDOCK:
  - optional `HPEPDOCK_EMAIL`.
- Boltz:
  - optional `BOLTZ_EXE`.
- MD:
  - optional `openmm` in the Python environment used by ChimeraX.

See `RAPIDOCK_SETUP.md` for the current RAPiDock setup notes.

## Wheel Installs

Use the wheel only if it was rebuilt from the same Git commit being shared:

```chimerax
toolshed install /absolute/path/to/ChimeraXbridge/dist/chimerax_codexbridge-0.1.0-py3-none-any.whl
```

If ChimeraX refuses local wheel paths, or if `scripts/check_release_ready.py`
reports missing files in the wheel, use `devel install`.

## Updating Later

```bash
cd ChimeraXbridge
git pull
```

Then rerun:

```chimerax
devel install /absolute/path/to/ChimeraXbridge
```

Restart ChimeraX after reinstalling.
