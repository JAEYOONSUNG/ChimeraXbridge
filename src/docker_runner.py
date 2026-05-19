import csv
import hashlib
import os
import re
import shlex
from pathlib import Path
import subprocess
import threading
import time


RAPIDOCK_DOCKER_IMAGE = "chimerax-codex-rapidock:cpu-amd64"
RAPIDOCK_DONE_FLAG = ".codex_rapidock_done"
RAPIDOCK_CONTAINER_PREFIX = "codex-rapidock-"


def _container_name_for(receptor_path, peptide):
    seed = f"{Path(receptor_path).resolve()}::{peptide}::{int(time.time())}".encode("utf-8")
    digest = hashlib.sha1(seed).hexdigest()[:10]
    return f"{RAPIDOCK_CONTAINER_PREFIX}{digest}"


def cleanup_orphan_containers(session=None):
    """Critic P3: remove any leftover stopped RAPiDock containers from prior runs."""
    try:
        listing = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name={RAPIDOCK_CONTAINER_PREFIX}",
             "--filter", "status=exited", "-q"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return
    ids = [line.strip() for line in (listing.stdout or "").splitlines() if line.strip()]
    if not ids:
        return
    try:
        subprocess.run(["docker", "rm", *ids], capture_output=True, text=True, timeout=30)
    except Exception:
        return
    if session is not None:
        try:
            session.logger.info(f"Cleaned up {len(ids)} orphan RAPiDock container(s).")
        except Exception:
            pass


def _container_is_running(name):
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", f"name=^{re.escape(name)}$", "-q"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return False
    return bool((result.stdout or "").strip())


def _docker_available():
    try:
        result = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except Exception:
        return False
    return result.returncode == 0


def _log(session, message, *, error=False):
    logger = getattr(session, "logger", None)
    if logger is None:
        return
    try:
        if error:
            logger.error(message)
        else:
            logger.info(message)
    except Exception:
        pass


def _quote_command_token(value):
    text = str(value)
    if not text:
        return "''"
    if all(ch.isalnum() or ch in "/._:=,+@%-" for ch in text):
        return text
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _image_exists():
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", RAPIDOCK_DOCKER_IMAGE],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except Exception:
        return False
    return result.returncode == 0


def _stream_process(session, process, prefix):
    if process.stdout is None:
        return
    for raw_line in iter(process.stdout.readline, ""):
        line = raw_line.rstrip()
        if line:
            _log(session, f"{prefix} {line}")


def _build_rapidock_docker_image(session, repo_root):
    if _image_exists():
        _log(session, f"RAPiDock Docker image already present: {RAPIDOCK_DOCKER_IMAGE}")
        return True, "cached"

    repo_root = Path(repo_root).expanduser().resolve()
    command = [
        "docker",
        "build",
        "--platform",
        "linux/amd64",
        "-t",
        RAPIDOCK_DOCKER_IMAGE,
        str(repo_root),
    ]
    _log(session, "Building RAPiDock Docker image: " + " ".join(_quote_command_token(part) for part in command))
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception as err:
        return False, f"docker build failed to launch: {err}"

    _stream_process(session, process, "[RAPiDock docker build]")
    # 2-hour cap: image rebuild + model downloads can be slow on first run,
    # but if the docker daemon hangs we surface a clear error instead of
    # blocking the worker thread forever.
    try:
        rc = process.wait(timeout=7200)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except Exception:
            pass
        return False, "docker build timed out after 2 hours; check Docker daemon and retry"
    elapsed = time.monotonic() - started
    if rc != 0:
        return False, f"docker build exited {rc} after {elapsed:.1f}s"
    _log(session, f"RAPiDock Docker image built as {RAPIDOCK_DOCKER_IMAGE} in {elapsed:.1f}s")
    return True, f"built in {elapsed:.1f}s"


def _container_path_for_run_file(path, run_dir):
    path = Path(path).expanduser().resolve()
    run_dir = Path(run_dir).expanduser().resolve()
    try:
        rel = path.relative_to(run_dir)
    except ValueError:
        return str(path)
    return "/work/" + rel.as_posix()


def _write_docker_csv(csv_path, run_dir):
    csv_path = Path(csv_path).expanduser().resolve()
    docker_csv_path = csv_path.with_name(csv_path.stem + "_docker.csv")
    run_dir = Path(run_dir).expanduser().resolve()
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fieldnames = reader.fieldnames or ["complex_name", "protein_description", "peptide_description"]
    for row in rows:
        protein = row.get("protein_description")
        if protein and Path(protein).expanduser().exists():
            row["protein_description"] = _container_path_for_run_file(protein, run_dir)
        peptide = row.get("peptide_description")
        if peptide and Path(peptide).expanduser().exists():
            row["peptide_description"] = _container_path_for_run_file(peptide, run_dir)
    with docker_csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return docker_csv_path


def _run_rapidock_in_docker(
    session,
    csv_path,
    output_dir,
    repo_root,
    n_samples,
    *,
    mode="global",
    batch_size=4,
    inference_steps=16,
    actual_steps=16,
    cpu=10,
    container_name=None,
    memory_limit_gib=10,
    receptor_pdb=None,
    peptide=None,
):
    """Critic-hardened docker run.

    P1: mount only repo_root (ro) and run_dir (rw); never $HOME.
    P3: name the container so we can detect duplicates and clean up.
    P5: pin HF_HOME / TORCH_HOME inside the mounted run_dir.
    P6: container's command appends a `done.flag` after inference completes
        so the watcher only attempts to load fully-written PDBs.
    """
    repo_root = Path(repo_root).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    run_dir = output_dir.parent
    docker_csv_path = _write_docker_csv(csv_path, run_dir)
    docker_csv = _container_path_for_run_file(docker_csv_path, run_dir)

    cleanup_orphan_containers(session)
    if container_name is None:
        container_name = _container_name_for(receptor_pdb or csv_path, peptide or "peptide")
    if _container_is_running(container_name):
        raise RuntimeError(
            f"A RAPiDock container with the same fingerprint is already running ({container_name}). "
            "Wait for it to finish or `docker stop` it manually."
        )

    cache_dir = run_dir / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    done_flag_host = output_dir / RAPIDOCK_DONE_FLAG
    if done_flag_host.exists():
        done_flag_host.unlink()

    inner_cmd = [
        "python", "inference.py",
        "--protein_peptide_csv", docker_csv,
        "--output_dir", "/work/output",
        "--N", str(int(n_samples)),
        "--model_dir", "train_models/CGTensorProductEquivariantModel",
        "--ckpt", f"rapidock_{mode}.pt",
        "--batch_size", str(int(batch_size)),
        "--no_final_step_noise",
        "--inference_steps", str(int(inference_steps)),
        "--actual_steps", str(int(actual_steps)),
        "--conformation_partial", "1:1:1",
        "--cpu", str(int(cpu)),
    ]
    # If PyRosetta is available in the image (set by `ai-setup-pyrosetta` button),
    # turn on ref2015 scoring + FastRelax so a ref2015_score.csv is produced.
    if os.environ.get("RAPIDOCK_USE_REF2015"):
        inner_cmd.extend(["--scoring_function", "ref2015", "--fastrelax"])
    inner_cmd_text = " ".join(shlex.quote(p) for p in inner_cmd)
    # P6: only write done.flag if inference exits 0
    shell_cmd = (
        f"set -e; "
        f"{inner_cmd_text}; "
        f"touch /work/output/{RAPIDOCK_DONE_FLAG}"
    )

    uid_gid = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else None
    command = [
        "docker", "run",
        "--rm",
        "--name", container_name,
        "--platform", "linux/amd64",
        "--memory", f"{int(memory_limit_gib)}g",
        "--memory-swap", f"{int(memory_limit_gib)}g",
        # The Dockerfile sets ENTRYPOINT=["python","/app/inference.py"], so we
        # must explicitly override it when we want to run a shell command.
        # Without this, the bash invocation gets appended as args to inference.py
        # and the run dies immediately with "unrecognized arguments".
        "--entrypoint", "/bin/bash",
        # /app must be writable -- inference.py's so3 cache (.so3_omegas_array2.npy
        # etc.) is created on first run inside the working directory. With :ro
        # the script aborts with `OSError: [Errno 30] Read-only file system`.
        # Caching the so3 files on the host repo avoids regeneration on every
        # run (~3 minutes saved).
        "-v", f"{repo_root}:/app",
        "-v", f"{run_dir}:/work",                 # writable run dir
        "-w", "/app",
        "-e", "HF_HOME=/work/.cache/hf",          # P5: keep ESM-2 in mounted folder
        "-e", "TORCH_HOME=/work/.cache/torch",
        "-e", "TRANSFORMERS_CACHE=/work/.cache/hf",
        "-e", "PYTHONUNBUFFERED=1",
    ]
    if uid_gid:
        command.extend(["--user", uid_gid])       # P1: avoid root-owned outputs
    command.extend([
        RAPIDOCK_DOCKER_IMAGE,
        "-c", shell_cmd,
    ])

    command_text = " ".join(_quote_command_token(part) for part in command)
    _log(session, f"Starting RAPiDock Docker inference (container {container_name}): {command_text}")
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception as err:
        raise RuntimeError(f"docker run failed to launch: {err}") from err
    process._codex_command_text = command_text
    process._codex_started = started
    process._codex_docker_csv = str(docker_csv_path)
    process._codex_container_name = container_name
    return process, command_text


def monitor_rapidock_docker(session, process, output_dir, on_success):
    lines = []
    if process.stdout is not None:
        for raw_line in iter(process.stdout.readline, ""):
            line = raw_line.rstrip()
            if not line:
                continue
            lines.append(line)
            if len(lines) > 200:
                lines = lines[-200:]
            _log(session, f"[RAPiDock docker] {line}")
    # 4-hour cap: a single CPU-only RAPiDock pose can take ~30 min, allow
    # 4× for safety. Beyond that, the docker container is likely stuck.
    try:
        rc = process.wait(timeout=14400)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except Exception:
            pass
        rc = -1
        _log(session, "RAPiDock Docker prediction killed after 4-hour timeout.", error=True)
    elapsed = time.monotonic() - getattr(process, "_codex_started", time.monotonic())
    if rc != 0:
        detail = "\n".join(lines[-80:])
        _log(
            session,
            "RAPiDock Docker prediction failed.\n"
            f"Command: {getattr(process, '_codex_command_text', '')}\n"
            f"Exit code: {rc}\n"
            f"Elapsed: {elapsed:.1f}s\n"
            f"{detail}",
            error=True,
        )
        return
    _log(session, f"RAPiDock Docker inference completed in {elapsed:.1f}s; outputs at {output_dir}")
    on_success()
