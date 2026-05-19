# RAPiDock Setup Verification

Verification date: 2026-04-30

## Repository

Tested clone URLs:

- `https://github.com/huifengzhao/RAPiDock`
- `https://github.com/PDB-CDDI/RAPiDock`
- `https://github.com/HSGuo/rapidock`

All clone attempts failed in the Codex shell environment with DNS resolution errors for `github.com`, so no RAPiDock source checkout was available in `~/RAPiDock`.

Expected key entries after a successful checkout:

- `inference.py`
- `requirements.txt`
- `train_models/CGTensorProductEquivariantModel/`

These entries were missing locally because the clone did not complete.

## Checkpoints

README/web lookup identified the expected checkpoint files:

- `rapidock_global.pt`
- `rapidock_local.pt`

The documented location is `train_models/CGTensorProductEquivariantModel/`. Zenodo record `10.5281/zenodo.14193621` lists both checkpoint files, each about 56.6 MB.

Attempted direct download:

```bash
curl -L --connect-timeout 20 --max-time 60 \
  -o ~/RAPiDock_download_probe/rapidock_global.pt \
  "https://zenodo.org/records/14193621/files/rapidock_global.pt?download=1"
```

This failed with `curl: (6) Could not resolve host: zenodo.org`.

## Virtual Environment

The requested command was attempted:

```bash
python3 -m venv ~/RAPiDock/.venv
```

In this shell, `python3` resolved to `/usr/local/bin/python3` and exited with status 137 even for `python3 --version`, so the requested command did not create a venv.

A fallback venv was created with Homebrew Python for local verification:

```bash
mkdir -p ~/RAPiDock
/opt/homebrew/bin/python3 -m venv ~/RAPiDock/.venv
```

Fallback venv Python:

```text
Python 3.13.3
```

Dependency installation could not run because `~/RAPiDock/requirements.txt` was missing.

## Tested Inference Command

Input CSV used:

```text
/tmp/test_input.csv
```

It was copied from:

```text
~/Downloads/ChimeraX/RAPiDock/codex_rapidock_20260430_005215/input/virtual_screening.csv
```

Command attempted:

```bash
cd ~/RAPiDock && ~/RAPiDock/.venv/bin/python inference.py \
  --protein_peptide_csv /tmp/test_input.csv \
  --output_dir /tmp/test_output \
  --ckpt rapidock_global.pt \
  --model_dir train_models/CGTensorProductEquivariantModel
```

Observed result:

```text
can't open file '<home>/RAPiDock/inference.py': [Errno 2] No such file or directory
inference_exit=2
wall_seconds=0
```

No `/tmp/test_output` directory and no `.pdb`, `.sdf`, or `.cif` output files were produced.

## ChimeraX Bridge Verification

The RAPiDock launcher now selects an interpreter in this order:

1. `<repo>/.venv/bin/python`
2. `<repo>/venv/bin/python`
3. `sys.executable`

Standalone verification of `src/toolbar_actions.py` with the fallback venv succeeded via `importlib`, and `_rapidock_output_files('/tmp/test_output')` returned `[]` because no inference outputs existed.
