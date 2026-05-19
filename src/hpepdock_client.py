"""HPEPDOCK 2.0 web service client.

Submits receptor + peptide sequence to http://huanglab.phys.hust.edu.cn/hpepdock,
polls until completion, downloads the result tarball, splits the multi-MODEL
PDB into rank1.pdb..rank5.pdb, and drops them into a directory that the
existing RAPiDock watcher monitors.

Why HPEPDOCK: RAPiDock cannot run on macOS (Linux ELF binary in upstream).
HPEPDOCK runs as a free Huang Lab web service (no GPU needed locally) and
returns top-100 peptide-receptor poses in 5–15 minutes.

Caveat: HPEPDOCK uses a hierarchical (non-diffusion) algorithm, so poses
will not match RAPiDock's published numbers. Surface this to the user.
"""

from __future__ import annotations

import io
import csv
import json
import math
import re
import shutil
import tarfile
import threading
import time
from collections import defaultdict
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

HPEPDOCK_BASE = "http://huanglab.phys.hust.edu.cn/hpepdock/"
HPEPDOCK_SUBMIT_URL = urljoin(HPEPDOCK_BASE, "runhpepdock.php")
HPEPDOCK_STATUS_PATH = "results.php"
# HPEPDOCK 2.0 packages results as top10/top100/all tarballs and also exposes
# individual model_N.pdb files. We prefer per-model downloads (avoid tarball
# extraction race conditions) but fall back to top10_models.tar.gz if needed.
HPEPDOCK_RESULT_TARBALLS = ("top10_models.tar.gz", "top100_models.tar.gz", "all_results.tar.gz")
HPEPDOCK_USER_AGENT = "ChimeraX-CodexBridge/1.0 (HPEPDOCK Mac fallback)"

_AA3_TO_1 = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
}
_ACIDIC_SIDECHAIN_O = {
    ("ASP", "OD1"), ("ASP", "OD2"), ("GLU", "OE1"), ("GLU", "OE2"),
}
_BASIC_SIDECHAIN_N = {
    ("LYS", "NZ"), ("ARG", "NE"), ("ARG", "NH1"), ("ARG", "NH2"),
    ("HIS", "ND1"), ("HIS", "NE2"),
}


class HpepdockError(RuntimeError):
    pass


def _multipart_body(fields):
    """Build a manual multipart/form-data body so we don't pull in `requests`.

    fields: list of tuples (name, filename_or_None, content_bytes, content_type)
            If filename is None, treats as a plain form field.
    """
    boundary = "----CodexBridgeHPEPDOCKBoundary" + str(int(time.time() * 1000))
    parts = []
    for name, filename, content, ctype in fields:
        if isinstance(content, str):
            content = content.encode("utf-8")
        header = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
        if filename is not None:
            header += f'; filename="{filename}"'
        header += f"\r\nContent-Type: {ctype}\r\n\r\n"
        parts.append(header.encode("utf-8"))
        parts.append(content)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    return body, boundary


DEFAULT_HPEPDOCK_EMAIL = "noreply@chimerax-codex-bridge.local"


def _looks_like_email(text):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", str(text or "").strip()))


def _read_pdb_for_upload(receptor_pdb_path):
    path = Path(receptor_pdb_path).expanduser()
    if not path.exists():
        raise HpepdockError(f"Receptor PDB not found: {path}")
    return path.read_bytes(), path.name


def submit_hpepdock(
    receptor_pdb_path,
    peptide_sequence,
    email=None,
    *,
    jobname=None,
    site_residues=None,
    n_models=100,
    n_pep_conformations=100,
    rigid=False,
    timeout=120,
):
    """Submit an HPEPDOCK job. Returns the job_id (string).

    Args:
        receptor_pdb_path: path to receptor PDB (or cropped pocket PDB).
        peptide_sequence: one-letter amino acid sequence (3–25 aa).
        email: optional. HPEPDOCK uses it for notification only — we poll the
            results page directly so the email is unused. If None or invalid,
            we send a placeholder so the form passes server validation.
        jobname: optional human-readable label.
        site_residues: optional comma-separated list like "A:42,A:43,A:80"
            to confine docking to a known site (HPEPDOCK "site" mode).
        n_models: total models requested (default 100, max 1000).
        n_pep_conformations: peptide conformations to generate (default 100).
        rigid: if True, disable peptide flexibility (faster).
        timeout: HTTP timeout in seconds.
    """
    if not _looks_like_email(email):
        email = DEFAULT_HPEPDOCK_EMAIL
    seq = re.sub(r"\s+", "", str(peptide_sequence or "")).upper()
    if not (3 <= len(seq) <= 25):
        raise HpepdockError(
            f"HPEPDOCK peptide must be 3–25 amino acids (got {len(seq)})."
        )
    if any(ch not in "ACDEFGHIKLMNPQRSTVWY" for ch in seq):
        raise HpepdockError(
            f"Peptide contains non-canonical amino acids: {sorted(set(seq) - set('ACDEFGHIKLMNPQRSTVWY'))}"
        )

    receptor_bytes, receptor_name = _read_pdb_for_upload(receptor_pdb_path)
    fasta_text = f">peptide\n{seq}\n"

    fields = [
        ("pdbfile1", receptor_name, receptor_bytes, "chemical/x-pdb"),
        ("pdbid1", None, "", "text/plain"),
        ("fastafile1", None, b"", "text/plain"),
        ("pdbfile2", None, b"", "chemical/x-pdb"),
        ("pdbid2", None, "", "text/plain"),
        ("fastafile2", "peptide.fasta", fasta_text, "text/plain"),
        ("docktyp", None, "site" if site_residues else "dock", "text/plain"),
        ("nmodpep", None, str(int(n_pep_conformations)), "text/plain"),
        ("nconf", None, str(int(n_models)), "text/plain"),
        ("email", None, email, "text/plain"),
        ("jobname", None, jobname or "ChimeraX-CodexBridge", "text/plain"),
        ("upload", None, "Submit", "text/plain"),
    ]
    if rigid:
        fields.append(("hpepdock", None, "rigid", "text/plain"))
    if site_residues:
        # sitenum1 = residue list ("195:A,203-206:A"). Do NOT also send
        # sitefile2 — HPEPDOCK will try to parse it as a reference PDB and
        # reject with "No atom or too many atoms" if it's empty.
        fields.append(("sitenum1", None, str(site_residues), "text/plain"))

    response_status = 0
    response_headers = {}
    response_text = ""
    response_url = HPEPDOCK_SUBMIT_URL
    try:
        import requests as _requests  # type: ignore
        files = {}
        data = {}
        for name, filename, content, ctype in fields:
            if filename is not None:
                if isinstance(content, str):
                    content = content.encode("utf-8")
                files[name] = (filename, content, ctype)
            else:
                data[name] = content
        resp = _requests.post(
            HPEPDOCK_SUBMIT_URL,
            files=files,
            data=data,
            headers={
                "User-Agent": HPEPDOCK_USER_AGENT,
                "Accept": "text/html",
                "Origin": HPEPDOCK_BASE.rstrip("/"),
                "Referer": HPEPDOCK_BASE,
            },
            timeout=timeout,
            allow_redirects=True,
        )
        response_url = resp.url
        response_status = resp.status_code
        response_headers = dict(resp.headers)
        response_text = resp.text
    except ImportError:
        body, boundary = _multipart_body(fields)
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
            "User-Agent": HPEPDOCK_USER_AGENT,
            "Accept": "text/html",
            "Origin": HPEPDOCK_BASE.rstrip("/"),
            "Referer": HPEPDOCK_BASE,
        }
        req = Request(HPEPDOCK_SUBMIT_URL, data=body, headers=headers, method="POST")
        try:
            with urlopen(req, timeout=timeout) as response:
                response_url = response.geturl()
                response_status = response.getcode()
                response_headers = dict(response.headers)
                response_text = response.read().decode("utf-8", errors="replace")
        except Exception as err:
            raise HpepdockError(f"HPEPDOCK submission failed (urllib): {err}") from err
    except Exception as err:
        raise HpepdockError(f"HPEPDOCK submission failed (requests): {err}") from err

    job_id = _extract_job_id(response_url, response_text)
    if not job_id:
        ctype = response_headers.get("Content-Type", "?")
        clen = response_headers.get("Content-Length", str(len(response_text)))
        snippet = response_text[:1500]
        raise HpepdockError(
            "HPEPDOCK submission did not return a job ID.\n"
            f"  URL: {response_url}\n"
            f"  HTTP {response_status}, Content-Type {ctype}, length {clen}\n"
            f"  Body length: {len(response_text)} chars\n"
            f"  First 1500 chars: {snippet!r}"
        )
    return job_id


def _extract_job_id(response_url, response_text):
    # HPEPDOCK 2.0 sometimes redirects to /hpepdock/data/<job_id>/, but more
    # often returns the job results page directly with the runhpepdock.php URL.
    # In that case the job_id appears in the HTML title and various links.
    text = response_text or ""
    url = response_url or ""

    # Title pattern: "Job results for 69f44c3813410"
    m = re.search(r"Job results for\s+([a-f0-9]{10,20})", text, re.IGNORECASE)
    if m:
        return m.group(1)
    # URL/path pattern: /hpepdock/data/<job_id>/...
    for src in (url, text):
        m = re.search(r"/hpepdock/data/([a-f0-9]{10,20})(?:/|\b)", src)
        if m:
            return m.group(1)
    # Legacy query-string fallback
    for pattern in (r"jobid=([A-Za-z0-9_\-]+)", r"jobname=([A-Za-z0-9_\-]+)"):
        m = re.search(pattern, url)
        if m:
            return m.group(1)
        m = re.search(pattern, text)
        if m:
            return m.group(1)
    return None


def status_url(job_id):
    # HPEPDOCK 2.0 status page lives under data/<job_id>/
    return urljoin(HPEPDOCK_BASE, f"data/{job_id}/")


def model_pdb_url(job_id, model_index):
    return urljoin(HPEPDOCK_BASE, f"data/{job_id}/model_{int(model_index)}.pdb")


def tarball_url(job_id, name):
    return urljoin(HPEPDOCK_BASE, f"data/{job_id}/{name}")


def fetch_status(job_id, *, timeout=60):
    """Return raw HTML for the status page (used to detect completion)."""
    req = Request(status_url(job_id), headers={"User-Agent": HPEPDOCK_USER_AGENT})
    try:
        with urlopen(req, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception as err:
        raise HpepdockError(f"HPEPDOCK status fetch failed for {job_id}: {err}") from err


def is_job_done(status_html):
    """Heuristic: completed jobs surface model_1.pdb and tarball download links."""
    text = (status_html or "").lower()
    return any(token in text for token in (
        "model_1.pdb",
        "top10_models.tar.gz",
        "top100_models.tar.gz",
        "all_results.tar.gz",
        "ranked binding models",
    ))


def parse_job_state(status_html):
    """Return one of 'queued','running','finished','error','unknown'."""
    text = (status_html or "")
    lower = text.lower()
    if is_job_done(text):
        return "finished"
    if "is queued" in lower or "in queue" in lower:
        return "queued"
    if "is running" in lower or "in progress" in lower or "calculating" in lower:
        return "running"
    if "error" in lower and "errors" not in lower[:200]:
        return "error"
    return "unknown"


def result_ready(job_id, *, top_n=1, timeout=20):
    # Probe both the first pose and the highest-rank pose. HPEPDOCK writes
    # models in order, so the top-N file existing means all 1..N are written.
    # Probing only model_1 races against partial publication and would let
    # download_individual_models break early at the first missing model_K.
    indices = (1,) if int(top_n) <= 1 else (1, int(top_n))
    for index in indices:
        req = Request(model_pdb_url(job_id, index), headers={"User-Agent": HPEPDOCK_USER_AGENT})
        try:
            with urlopen(req, timeout=timeout) as response:
                head = response.read(4096)
        except Exception:
            return False
        if not head:
            return False
        if not (b"ATOM" in head or b"MODEL" in head or b"HEADER" in head):
            return False
    return True


def download_individual_models(job_id, output_dir, *, top_n=5, timeout=120):
    """Download model_1.pdb..model_N.pdb directly from the HPEPDOCK results page.

    These files are pre-ranked (model_1 = best HPEPDOCK score). Returns list
    of paths actually written. Skips silently on 404 to handle short jobs.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for index in range(1, int(top_n) + 1):
        url = model_pdb_url(job_id, index)
        target = output_dir / f"rank{index}.pdb"
        req = Request(url, headers={"User-Agent": HPEPDOCK_USER_AGENT})
        try:
            with urlopen(req, timeout=timeout) as response:
                payload = response.read()
        except Exception as err:
            if index == 1:
                raise HpepdockError(
                    f"HPEPDOCK model_{index}.pdb download failed: {err}"
                ) from err
            break
        if not payload or b"ATOM" not in payload[:4096]:
            if index == 1:
                raise HpepdockError(
                    f"HPEPDOCK model_{index}.pdb did not contain ATOM records "
                    f"(content size {len(payload)} bytes)"
                )
            break
        target.write_bytes(payload)
        written.append(target)
    return written


def download_result_tarball(job_id, target_path, *, timeout=600):
    """Backward-compatible tarball download — tries top10_models.tar.gz, etc."""
    target_path = Path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    last_err = None
    for name in HPEPDOCK_RESULT_TARBALLS:
        req = Request(tarball_url(job_id, name), headers={"User-Agent": HPEPDOCK_USER_AGENT})
        try:
            with urlopen(req, timeout=timeout) as response, target_path.open("wb") as fh:
                chunk = response.read(1024 * 256)
                while chunk:
                    fh.write(chunk)
                    chunk = response.read(1024 * 256)
        except Exception as err:
            last_err = err
            continue
        if target_path.exists() and target_path.stat().st_size > 5_000:
            return target_path
    raise HpepdockError(
        f"Failed to download any HPEPDOCK tarball ({HPEPDOCK_RESULT_TARBALLS}). Last error: {last_err}"
    )


def split_multimodel_pdb(multi_pdb_text, output_dir, *, top_n=5, prefix="rank"):
    """Split a multi-MODEL PDB string into output_dir/rankN.pdb files.

    HPEPDOCK's hpepdock_all.pdb concatenates docking poses with MODEL/ENDMDL
    markers in score order (best first). Returns the list of written paths
    (up to top_n).
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    current = []
    in_model = False
    for line in multi_pdb_text.splitlines():
        if line.startswith("MODEL"):
            in_model = True
            current = []
            continue
        if line.startswith("ENDMDL"):
            in_model = False
            if current:
                idx = len(written) + 1
                target = output_dir / f"{prefix}{idx}.pdb"
                target.write_text("\n".join(current) + "\nEND\n")
                written.append(target)
                if len(written) >= int(top_n):
                    break
            continue
        if in_model:
            current.append(line)
    if not written and not in_model:
        # No MODEL records — treat as a single PDB and write it as rank1.
        text = multi_pdb_text.strip()
        if text:
            target = output_dir / f"{prefix}1.pdb"
            target.write_text(text + "\nEND\n")
            written.append(target)
    return written


def extract_results(tarball_path, output_dir, *, top_n=5):
    """Extract HPEPDOCK pose PDBs from the tarball.

    HuangLab tarballs contain `<jobid>/model_N.pdb` files in score-ranked order
    (or a single multi-MODEL `hpepdock_all.pdb`, depending on server version).
    Returns list of paths written as rank1.pdb..rankN.pdb.
    """
    import re
    tarball_path = Path(tarball_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    multi_pdb_text = None
    score_text = None
    per_model = {}
    with tarfile.open(tarball_path, "r:gz") as tar:
        for member in tar.getmembers():
            name = Path(member.name).name
            if name == "hpepdock_all.pdb":
                f = tar.extractfile(member)
                if f is not None:
                    multi_pdb_text = f.read().decode("utf-8", errors="replace")
            elif name == "hpepdock_all.out":
                f = tar.extractfile(member)
                if f is not None:
                    score_text = f.read().decode("utf-8", errors="replace")
            else:
                m = re.match(r"model_(\d+)\.pdb$", name)
                if m:
                    f = tar.extractfile(member)
                    if f is not None:
                        per_model[int(m.group(1))] = f.read()

    if score_text:
        (output_dir / "hpepdock_all.out").write_text(score_text)

    if per_model:
        written = []
        for rank, idx in enumerate(sorted(per_model.keys())[: int(top_n)], start=1):
            target = output_dir / f"rank{rank}.pdb"
            target.write_bytes(per_model[idx])
            written.append(target)
        return written

    if multi_pdb_text is not None:
        return split_multimodel_pdb(multi_pdb_text, output_dir, top_n=top_n)

    raise HpepdockError(
        f"Tarball {tarball_path.name} contained no model_*.pdb or hpepdock_all.pdb"
    )


def extract_hpepdock_metadata(tarball_path, output_dir):
    """Extract non-pose metadata files from a HuangLab result tarball.

    HPEPDOCK's direct model_N.pdb files usually contain only peptide atoms.
    The tarball has the score table and, on current server builds, a
    multi-model PDB with REMARK ITScore/RMSD records. We keep these beside the
    rank PDBs so the local result package is self-describing.
    """
    tarball_path = Path(tarball_path)
    output_dir = Path(output_dir)
    if not tarball_path.exists():
        return []
    written = []
    wanted_exact = {"hpepdock_all.out", "hpepdock_all.pdb", "hpepdock_all.fasta"}
    with tarfile.open(tarball_path, "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            name = Path(member.name).name
            if not (
                name in wanted_exact
                or re.match(r"hpepdock_[A-Za-z0-9_-]+\.out$", name)
                or re.match(r"rec_[A-Za-z0-9_-]+\.pdb$", name)
            ):
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            parts = [part for part in Path(member.name).parts if part not in ("", ".", "..")]
            if len(parts) > 1:
                target = output_dir.joinpath(*parts[-2:])
            else:
                target = output_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(handle.read())
            written.append(target)
            if name == "hpepdock_all.out":
                flat = output_dir / "hpepdock_all.out"
                if flat != target:
                    flat.write_bytes(target.read_bytes())
                    written.append(flat)
    return written


def _rank_from_path(path):
    match = re.search(r"(?:^|[_-])rank[_-]?(\d+)", Path(path).stem.lower())
    if match:
        return int(match.group(1))
    match = re.search(r"model[_-]?(\d+)", Path(path).stem.lower())
    if match:
        return int(match.group(1))
    numbers = re.findall(r"\d+", Path(path).stem)
    return int(numbers[0]) if numbers else 999999


def _looks_like_hpepdock_dir(root):
    root = Path(root)
    if not root.exists():
        return False
    markers = (
        "hpepdock_all.out",
        "hpepdock_all.pdb",
        "hpepdock_top.tar.gz",
        "top10_models.tar.gz",
        "top100_models.tar.gz",
        "all_results.tar.gz",
    )
    for marker in markers:
        if any(root.rglob(marker)):
            return True
    return any(root.rglob("model_*.pdb")) or any(root.rglob("rank*.pdb"))


def _hpepdock_pose_files(root, *, top_n=5):
    root = Path(root).expanduser()
    if not root.exists():
        return []
    candidates = []
    for pattern in ("rank*.pdb", "model_*.pdb"):
        for path in root.rglob(pattern):
            stem = path.stem.lower()
            if (
                not path.is_file()
                or path.name.startswith(".")
                or stem.endswith("_complex")
                or "_minimized" in stem
                or "_refined" in stem
                or stem.startswith("rec_")
                or stem == "hpepdock_all"
            ):
                continue
            candidates.append(path)
    dedup = {}
    for path in sorted(candidates, key=lambda item: (_rank_from_path(item), len(item.parts), item.name.lower())):
        dedup.setdefault(_rank_from_path(path), path)
    return [dedup[key] for key in sorted(dedup)[: max(1, int(top_n or 5))]]


def _parse_hpepdock_score_table(root):
    scores = {}
    root = Path(root)
    for table in sorted(root.rglob("hpepdock_all.out"), key=lambda item: (len(item.parts), str(item))):
        try:
            lines = table.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 4:
                continue
            try:
                cluster = int(parts[0])
                rank = int(parts[1])
                score = float(parts[3])
            except Exception:
                continue
            entry = scores.setdefault(rank, {})
            entry.update({
                "cluster": cluster,
                "rank": rank,
                "ligand": parts[2],
                "itscore": score,
            })
            if len(parts) >= 5:
                entry["peptide_sequence"] = parts[4]
    return scores


def _parse_hpepdock_remark_scores(root):
    scores = {}
    current = None
    for pdb_path in sorted(Path(root).rglob("hpepdock_all.pdb"), key=lambda item: (len(item.parts), str(item))):
        try:
            lines = pdb_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for line in lines:
            if not line.startswith("REMARK"):
                continue
            number = re.search(r"Number:\s*(\d+)", line)
            if number:
                current = int(number.group(1))
                scores.setdefault(current, {})["rank"] = current
                continue
            if current is None:
                continue
            itscore = re.search(r"ITScore\s*:\s*(-?\d+(?:\.\d+)?)", line)
            if itscore:
                scores.setdefault(current, {})["itscore"] = float(itscore.group(1))
                continue
            rmsd = re.search(r"RMSD\s*:\s*(-?\d+(?:\.\d+)?)", line)
            if rmsd:
                scores.setdefault(current, {})["rmsd"] = float(rmsd.group(1))
    return scores


def parse_hpepdock_scores(root):
    scores = _parse_hpepdock_score_table(root)
    for rank, values in _parse_hpepdock_remark_scores(root).items():
        scores.setdefault(rank, {}).update({k: v for k, v in values.items() if v is not None})
    return scores


def _pdb_element(line, atom_name):
    element = line[76:78].strip() if len(line) >= 78 else ""
    if element:
        return element.upper()
    name = re.sub(r"^[0-9]+", "", atom_name.strip())
    if not name:
        return ""
    if len(name) >= 2 and name[0].isalpha() and name[1].islower():
        return name[:2].upper()
    return name[0].upper()


def _read_pdb_atoms(path, *, role):
    atoms = []
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return atoms
    for line in lines:
        if not line.startswith(("ATOM", "HETATM")):
            continue
        try:
            atom_name = line[12:16].strip()
            resname = line[17:20].strip().upper()
            chain = (line[21:22].strip() or "_")
            resseq = int(line[22:26])
            icode = line[26:27].strip()
            x = float(line[30:38])
            y = float(line[38:46])
            z = float(line[46:54])
        except Exception:
            continue
        element = _pdb_element(line, atom_name)
        atoms.append({
            "line": line.rstrip("\n"),
            "record": line[:6].strip(),
            "role": role,
            "atom": atom_name,
            "resname": resname,
            "chain": chain,
            "resseq": resseq,
            "icode": icode,
            "x": x,
            "y": y,
            "z": z,
            "element": element,
            "heavy": element != "H",
        })
    return atoms


def _residue_key(atom):
    return (atom["chain"], atom["resseq"], atom["icode"], atom["resname"])


def _residue_label(key):
    chain, resseq, icode, resname = key
    suffix = icode or ""
    return f"{chain}:{resseq}{suffix} {resname}"


def _chain_summary(atoms):
    residues = defaultdict(set)
    for atom in atoms:
        if atom.get("record") != "ATOM":
            continue
        residues[atom["chain"]].add((atom["resseq"], atom["icode"], atom["resname"]))
    parts = []
    for chain in sorted(residues):
        nums = sorted(residues[chain])
        if not nums:
            continue
        lo = f"{nums[0][0]}{nums[0][1] or ''}"
        hi = f"{nums[-1][0]}{nums[-1][1] or ''}"
        parts.append(f"{chain}:{len(nums)}res({lo}-{hi})")
    hetero = defaultdict(int)
    for atom in atoms:
        if atom.get("record") == "HETATM":
            hetero[atom["resname"]] += 1
    if hetero:
        parts.append("hetero:" + ",".join(f"{name}x{count}" for name, count in sorted(hetero.items())))
    return "; ".join(parts) or "n/a"


def _sequence_from_atoms(atoms):
    residues = []
    seen = set()
    for atom in sorted(atoms, key=lambda item: (item["chain"], item["resseq"], item["icode"])):
        key = _residue_key(atom)
        if key in seen:
            continue
        seen.add(key)
        residues.append(_AA3_TO_1.get(atom["resname"], "X"))
    return "".join(residues)


def _is_hbond_like(atom_a, atom_b, distance):
    if distance > 3.5:
        return False
    return atom_a["element"] in {"N", "O", "S"} and atom_b["element"] in {"N", "O", "S"}


def _is_salt_bridge(atom_a, atom_b, distance):
    if distance > 4.0:
        return False
    a = (atom_a["resname"], atom_a["atom"])
    b = (atom_b["resname"], atom_b["atom"])
    return (
        (a in _ACIDIC_SIDECHAIN_O and b in _BASIC_SIDECHAIN_N)
        or (a in _BASIC_SIDECHAIN_N and b in _ACIDIC_SIDECHAIN_O)
    )


_VDW_RADIUS = {
    "H": 1.20,
    "C": 1.70,
    "N": 1.55,
    "O": 1.52,
    "S": 1.80,
    "P": 1.80,
    "CA": 1.94,
    "MG": 1.73,
    "ZN": 1.39,
    "FE": 1.56,
    "CU": 1.40,
    "MN": 1.61,
}


def _vdw_radius(element):
    return float(_VDW_RADIUS.get(str(element or "").upper(), 1.70))


def _coords(atom):
    return (float(atom["x"]), float(atom["y"]), float(atom["z"]))


def _copy_pdb_atom_line_with_coord(line, coord, *, serial=None, chain=None):
    text = line.rstrip("\n").ljust(80)
    if serial is not None:
        text = f"{text[:6]}{int(serial):5d}{text[11:]}"
    if chain is not None and len(text) >= 22:
        text = text[:21] + str(chain)[:1] + text[22:]
    x, y, z = coord
    return f"{text[:30]}{x:8.3f}{y:8.3f}{z:8.3f}{text[54:]}".rstrip()


def _write_transformed_peptide_pdb(source_path, target_path, translation, *, chain=None):
    target_path = Path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tx, ty, tz = [float(v) for v in translation]
    lines = []
    serial = 1
    for line in Path(source_path).read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        try:
            coord = (
                float(line[30:38]) + tx,
                float(line[38:46]) + ty,
                float(line[46:54]) + tz,
            )
        except Exception:
            continue
        lines.append(_copy_pdb_atom_line_with_coord(line, coord, serial=serial, chain=chain))
        serial += 1
    target_path.write_text("\n".join(lines + ["TER", "END"]) + "\n", encoding="utf-8")
    return target_path


def _steric_relax_translation(receptor_atoms, peptide_atoms, *, max_steps=240):
    """Rigid-body peptide translation that reduces receptor-peptide steric clashes.

    This is intentionally conservative: receptor and peptide internal geometry
    are fixed, the peptide can move by at most a few angstroms, and a tether
    keeps the docked pose near the HPEPDOCK solution. It is a local clash
    relaxation/validation step, not a full force-field MM refinement.
    """
    rec = [atom for atom in receptor_atoms if atom.get("heavy")]
    pep = [atom for atom in peptide_atoms if atom.get("heavy")]
    if not rec or not pep:
        return {
            "translation": (0.0, 0.0, 0.0),
            "steps": 0,
            "energy_initial": 0.0,
            "energy_final": 0.0,
            "status": "skipped_no_atoms",
        }

    rec_items = [(*_coords(atom), _vdw_radius(atom.get("element"))) for atom in rec]
    pep_items = [(*_coords(atom), _vdw_radius(atom.get("element"))) for atom in pep]
    cell_size = 3.0
    grid = defaultdict(list)
    for item in rec_items:
        cx = math.floor(item[0] / cell_size)
        cy = math.floor(item[1] / cell_size)
        cz = math.floor(item[2] / cell_size)
        grid[(cx, cy, cz)].append(item)
    tether_weight = 0.18
    translation = [0.0, 0.0, 0.0]
    lr = 0.030
    max_step = 0.12
    max_translation = 2.50

    def energy_grad(vec):
        energy = tether_weight * (vec[0] * vec[0] + vec[1] * vec[1] + vec[2] * vec[2])
        grad = [2.0 * tether_weight * vec[0], 2.0 * tether_weight * vec[1], 2.0 * tether_weight * vec[2]]
        clash_count = 0
        for px0, py0, pz0, pr in pep_items:
            px = px0 + vec[0]
            py = py0 + vec[1]
            pz = pz0 + vec[2]
            ccx = math.floor(px / cell_size)
            ccy = math.floor(py / cell_size)
            ccz = math.floor(pz / cell_size)
            for dx_cell in (-1, 0, 1):
                for dy_cell in (-1, 0, 1):
                    for dz_cell in (-1, 0, 1):
                        for rx, ry, rz, rr in grid.get((ccx + dx_cell, ccy + dy_cell, ccz + dz_cell), ()):
                            dx = px - rx
                            dy = py - ry
                            dz = pz - rz
                            dist2 = dx * dx + dy * dy + dz * dz
                            if dist2 > 8.0:
                                continue
                            dist = math.sqrt(max(dist2, 1.0e-12))
                            target = min(2.75, max(2.05, 0.72 * (pr + rr)))
                            if dist <= 2.0:
                                clash_count += 1
                            if dist >= target:
                                continue
                            delta = dist - target
                            energy += delta * delta
                            scale = 2.0 * delta / dist
                            grad[0] += scale * dx
                            grad[1] += scale * dy
                            grad[2] += scale * dz
        return energy, grad, clash_count

    initial_energy, _grad, initial_clashes = energy_grad(translation)
    best = list(translation)
    best_energy = initial_energy
    status = "ok"
    steps_done = 0
    for step in range(max(0, int(max_steps))):
        energy, grad, clash_count = energy_grad(translation)
        norm = math.sqrt(sum(v * v for v in grad))
        tether_energy = tether_weight * sum(v * v for v in translation)
        if norm < 1.0e-5 or (clash_count == 0 and energy <= tether_energy + 1.0e-4):
            steps_done = step
            break
        move = [-lr * grad[0], -lr * grad[1], -lr * grad[2]]
        move_norm = math.sqrt(sum(v * v for v in move))
        if move_norm > max_step:
            scale = max_step / move_norm
            move = [v * scale for v in move]
        trial = [translation[0] + move[0], translation[1] + move[1], translation[2] + move[2]]
        trial_norm = math.sqrt(sum(v * v for v in trial))
        if trial_norm > max_translation:
            scale = max_translation / trial_norm
            trial = [v * scale for v in trial]
        trial_energy, _trial_grad, _trial_clashes = energy_grad(trial)
        if trial_energy <= energy or trial_energy <= best_energy:
            translation = trial
            if trial_energy < best_energy:
                best = list(trial)
                best_energy = trial_energy
            lr = min(0.060, lr * 1.04)
        else:
            lr *= 0.50
            if lr < 1.0e-5:
                steps_done = step
                break
        steps_done = step + 1
    final_energy, _final_grad, final_clashes = energy_grad(best)
    if final_energy > initial_energy:
        best = [0.0, 0.0, 0.0]
        final_energy = initial_energy
        final_clashes = initial_clashes
        status = "no_improvement"
    return {
        "translation": tuple(float(v) for v in best),
        "steps": int(steps_done),
        "energy_initial": float(initial_energy),
        "energy_final": float(final_energy),
        "initial_clashes": int(initial_clashes),
        "final_clashes": int(final_clashes),
        "status": status,
    }


def minimize_hpepdock_pose(
    receptor_pdb_path,
    pose_pdb_path,
    target_peptide_path,
    *,
    max_steps=240,
    force=False,
):
    target_peptide_path = Path(target_peptide_path)
    pose_pdb_path = Path(pose_pdb_path)
    receptor_atoms = _read_pdb_atoms(receptor_pdb_path, role="receptor")
    peptide_atoms = _read_pdb_atoms(pose_pdb_path, role="peptide")
    peptide_chain = _peptide_chain_override(receptor_atoms, peptide_atoms)
    result = _steric_relax_translation(receptor_atoms, peptide_atoms, max_steps=max_steps)
    translation = result.get("translation") or (0.0, 0.0, 0.0)
    _write_transformed_peptide_pdb(
        pose_pdb_path,
        target_peptide_path,
        translation,
        chain=peptide_chain,
    )
    result["pose_pdb"] = str(target_peptide_path)
    result["translation_a"] = math.sqrt(sum(float(v) * float(v) for v in translation))
    return result


def summarize_hpepdock_pose(receptor_pdb_path, pose_pdb_path, *, score_info=None):
    receptor_atoms = [atom for atom in _read_pdb_atoms(receptor_pdb_path, role="receptor") if atom["heavy"]]
    peptide_atoms = [atom for atom in _read_pdb_atoms(pose_pdb_path, role="peptide") if atom["heavy"]]
    peptide_chain = _peptide_chain_override(receptor_atoms, peptide_atoms)
    if peptide_chain:
        for atom in peptide_atoms:
            atom["chain"] = peptide_chain
    score_info = dict(score_info or {})
    pair_stats = {}
    contact_atom_pairs = 0
    hbond_like_pairs = 0
    salt_bridge_pairs = 0
    clash_atom_pairs = 0
    min_distance = None
    for ra in receptor_atoms:
        for pa in peptide_atoms:
            dx = ra["x"] - pa["x"]
            dy = ra["y"] - pa["y"]
            dz = ra["z"] - pa["z"]
            distance = math.sqrt(dx * dx + dy * dy + dz * dz)
            if min_distance is None or distance < min_distance:
                min_distance = distance
            if distance <= 2.0:
                clash_atom_pairs += 1
            if distance > 4.0:
                continue
            contact_atom_pairs += 1
            key = (_residue_key(ra), _residue_key(pa))
            stat = pair_stats.setdefault(key, {
                "min_distance": distance,
                "atom_contacts": 0,
                "hbond_like": 0,
                "salt_bridges": 0,
            })
            stat["atom_contacts"] += 1
            if distance < stat["min_distance"]:
                stat["min_distance"] = distance
            if _is_hbond_like(ra, pa, distance):
                stat["hbond_like"] += 1
                hbond_like_pairs += 1
            if _is_salt_bridge(ra, pa, distance):
                stat["salt_bridges"] += 1
                salt_bridge_pairs += 1

    ranked_pairs = sorted(
        pair_stats.items(),
        key=lambda item: (item[1]["min_distance"], -item[1]["atom_contacts"]),
    )
    top_pairs = []
    for (rec_key, pep_key), stat in ranked_pairs[:12]:
        top_pairs.append(
            f"{_residue_label(rec_key)}--{_residue_label(pep_key)} "
            f"{stat['min_distance']:.2f}A/{stat['atom_contacts']}ct"
        )
    receptor_interface = {_residue_label(keys[0]) for keys in pair_stats}
    peptide_interface = {_residue_label(keys[1]) for keys in pair_stats}
    rank = int(score_info.get("rank") or _rank_from_path(pose_pdb_path))
    return {
        "rank": rank,
        "pose_pdb": str(Path(pose_pdb_path)),
        "itscore": score_info.get("itscore"),
        "rmsd": score_info.get("rmsd"),
        "cluster": score_info.get("cluster"),
        "ligand": score_info.get("ligand"),
        "peptide_sequence": score_info.get("peptide_sequence") or _sequence_from_atoms(peptide_atoms),
        "receptor_chains": _chain_summary(receptor_atoms),
        "peptide_chains": _chain_summary(peptide_atoms),
        "peptide_length": len({_residue_key(atom) for atom in peptide_atoms}),
        "contact_residue_pairs_4a": len(pair_stats),
        "contact_atom_pairs_4a": contact_atom_pairs,
        "clash_atom_pairs_2a": clash_atom_pairs,
        "hbond_like_pairs_3_5a": hbond_like_pairs,
        "salt_bridges_4a": salt_bridge_pairs,
        "min_distance_a": min_distance,
        "receptor_interface_residues": "; ".join(sorted(receptor_interface)),
        "peptide_interface_residues": "; ".join(sorted(peptide_interface)),
        "top_interface_pairs": "; ".join(top_pairs),
    }


def _find_receptor_for_hpepdock(root, receptor_pdb_path=None):
    if receptor_pdb_path:
        path = Path(receptor_pdb_path).expanduser()
        if path.exists():
            return path
    root = Path(root).expanduser()
    candidates = []
    for parent in [root, *root.parents[:4]]:
        candidates.extend([
            parent / "input" / "receptor_cropped.pdb",
            parent / "receptor_cropped.pdb",
        ])
    for csv_path in root.rglob("virtual_screening.csv"):
        candidates.append(csv_path.parent / "receptor_cropped.pdb")
        try:
            with csv_path.open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    value = str(row.get("protein_description") or "").strip()
                    if value:
                        candidates.append(Path(value).expanduser())
        except Exception:
            pass
    candidates.extend(root.rglob("rec_*.pdb"))
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_file():
                return candidate
        except Exception:
            continue
    return None


def _unused_chain_id(used):
    for chain in "BCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
        if chain not in used:
            return chain
    return "Z"


def _peptide_chain_override(receptor_atoms, peptide_atoms):
    receptor_chains = {atom["chain"] for atom in receptor_atoms if atom["chain"] != "_"}
    peptide_chains = {atom["chain"] for atom in peptide_atoms if atom["chain"] != "_"}
    if peptide_chains & receptor_chains:
        return _unused_chain_id(receptor_chains)
    return None


def _pdb_remark(index, text):
    return f"REMARK {int(index):3d} {str(text).replace(chr(10), ' ')}"[:80].rstrip()


def _atom_lines_for_complex(path, *, peptide_chain=None, start_serial=1):
    lines = []
    serial = int(start_serial)
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        line = line.rstrip().ljust(80)
        line = f"{line[:6]}{serial:5d}{line[11:]}"
        if peptide_chain and len(line) >= 22:
            line = line[:21] + peptide_chain[:1] + line[22:]
        lines.append(line.rstrip())
        serial += 1
    return lines, serial


def build_hpepdock_complex_pdb(receptor_pdb_path, pose_pdb_path, target_path, *, score_info=None):
    receptor_atoms = _read_pdb_atoms(receptor_pdb_path, role="receptor")
    peptide_atoms = _read_pdb_atoms(pose_pdb_path, role="peptide")
    peptide_chain = _peptide_chain_override(receptor_atoms, peptide_atoms)
    score_info = dict(score_info or {})
    rank = int(score_info.get("rank") or _rank_from_path(pose_pdb_path))
    remarks = [
        _pdb_remark(1, f"Codex HPEPDOCK complex package rank {rank}"),
        _pdb_remark(2, f"Receptor source: {Path(receptor_pdb_path).name}"),
        _pdb_remark(3, f"Peptide pose source: {Path(pose_pdb_path).name}"),
    ]
    if score_info.get("itscore") is not None:
        remarks.append(_pdb_remark(4, f"HPEPDOCK ITScore: {float(score_info['itscore']):.3f}"))
    if score_info.get("rmsd") is not None:
        remarks.append(_pdb_remark(5, f"HPEPDOCK RMSD-to-top: {float(score_info['rmsd']):.3f}"))
    if score_info.get("minimization_status"):
        remarks.append(_pdb_remark(6, f"Codex minimization: {score_info.get('minimization_status')}"))
    if score_info.get("minimization_translation_a") not in (None, ""):
        remarks.append(_pdb_remark(7, f"Peptide rigid-body shift: {float(score_info['minimization_translation_a']):.3f} A"))
    receptor_lines, next_serial = _atom_lines_for_complex(receptor_pdb_path, start_serial=1)
    peptide_lines, _next_serial = _atom_lines_for_complex(
        pose_pdb_path,
        peptide_chain=peptide_chain,
        start_serial=next_serial,
    )
    target_path = Path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        "\n".join([*remarks, *receptor_lines, "TER", *peptide_lines, "TER", "END"]) + "\n",
        encoding="utf-8",
    )
    return target_path


def _as_float(value, default=None):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _as_int(value, default=0):
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except Exception:
        return default


def _evaluate_hpepdock_pose(item):
    itscore = _as_float(item.get("itscore"), 0.0)
    rank = _as_int(item.get("rank"), 999999)
    clashes = _as_int(item.get("clash_atom_pairs_2a"), 0)
    min_dist = _as_float(item.get("min_distance_a"), 999.0)
    shift = _as_float(item.get("minimization_translation_a"), 0.0)
    retained = _as_float(item.get("contact_retained_fraction"), 1.0)
    hbond_like = _as_int(item.get("hbond_like_pairs_3_5a"), 0)
    contacts = _as_int(item.get("contact_residue_pairs_4a"), 0)
    raw_clashes = _as_int(item.get("raw_clash_atom_pairs_2a"), 0)
    raw_min = _as_float(item.get("raw_min_distance_a"), min_dist)

    penalty = 0.0
    flags = []
    if clashes > 0:
        penalty += 80.0 * clashes
        flags.append(f"{clashes} clash(es) <=2A remain")
    if min_dist < 2.05:
        penalty += 120.0 * (2.05 - min_dist)
        flags.append(f"closest contact {min_dist:.2f}A")
    if shift > 2.0:
        penalty += 25.0 * (shift - 2.0)
        flags.append(f"large peptide shift {shift:.2f}A")
    elif shift > 1.25:
        penalty += 6.0 * (shift - 1.25)
        flags.append(f"moderate peptide shift {shift:.2f}A")
    if retained < 0.50:
        penalty += 45.0 * (0.50 - retained)
        flags.append(f"low contact retention {retained:.2f}")
    elif retained < 0.70:
        penalty += 8.0 * (0.70 - retained)
        flags.append(f"reduced contact retention {retained:.2f}")
    if contacts < 5:
        penalty += 15.0
        flags.append(f"few interface residue pairs ({contacts})")
    if hbond_like == 0:
        penalty += 2.0
        flags.append("no H-bond-like polar contacts")
    if raw_clashes > 0 and clashes == 0:
        flags.append(f"relaxed raw clash(es) {raw_clashes}->0")
    if raw_min < 2.0 <= min_dist:
        flags.append(f"closest contact improved {raw_min:.2f}->{min_dist:.2f}A")

    status = "pass"
    if clashes > 0 or min_dist < 2.0 or shift > 2.5 or retained < 0.35 or contacts < 3:
        status = "reject"
    elif shift > 1.25 or retained < 0.70 or hbond_like == 0:
        status = "review"
    if item.get("minimization_status") not in ("ok", "disabled", None, ""):
        status = "review" if status == "pass" else status
        flags.append(f"minimization status {item.get('minimization_status')}")

    refined_score = itscore + penalty if itscore is not None else float(rank) + penalty
    return {
        "validation_status": status,
        "validation_flags": "; ".join(flags) or "none",
        "refined_score": refined_score,
    }


def _annotate_hpepdock_recommendations(summaries):
    for item in summaries:
        item.update(_evaluate_hpepdock_pose(item))
    status_order = {"pass": 0, "review": 1, "reject": 2}
    ranked = sorted(
        summaries,
        key=lambda item: (
            status_order.get(item.get("validation_status"), 9),
            _as_float(item.get("refined_score"), 1.0e9),
            _as_int(item.get("rank"), 999999),
        ),
    )
    for index, item in enumerate(ranked, start=1):
        item["validation_rank"] = index
        item["recommended"] = "yes" if index == 1 and item.get("validation_status") != "reject" else "no"
    return ranked


def _best_hpepdock_summary(summaries):
    ranked = sorted(summaries, key=lambda item: _as_int(item.get("validation_rank"), 999999))
    return ranked[0] if ranked else None


def _cxc_quote(path):
    text = str(path)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _first_chain_from_summary(text):
    for part in str(text or "").split(";"):
        part = part.strip()
        if not part or part.startswith("hetero:") or ":" not in part:
            continue
        return part.split(":", 1)[0].strip()
    return ""


def _write_hpepdock_auxiliary_outputs(case_dir, summaries, *, receptor_pdb_path=None):
    case_dir = Path(case_dir)
    best = _best_hpepdock_summary(summaries)
    outputs = {}
    recommended_tsv = case_dir / "codex_hpepdock_recommended.tsv"
    rec_fields = [
        "validation_rank", "rank", "validation_status", "recommended", "refined_score",
        "itscore", "minimization_translation_a", "raw_clash_atom_pairs_2a",
        "clash_atom_pairs_2a", "raw_min_distance_a", "min_distance_a",
        "contact_retained_fraction", "validation_flags", "complex_pdb",
    ]
    with recommended_tsv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rec_fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for item in sorted(summaries, key=lambda row: _as_int(row.get("validation_rank"), 999999)):
            row = dict(item)
            for key in ("refined_score", "itscore", "minimization_translation_a", "contact_retained_fraction"):
                if row.get(key) not in (None, ""):
                    row[key] = f"{float(row[key]):.3f}"
            for key in ("raw_min_distance_a", "min_distance_a"):
                if row.get(key) not in (None, ""):
                    row[key] = f"{float(row[key]):.2f}"
            writer.writerow(row)
    outputs["recommended_tsv"] = recommended_tsv

    methods_md = case_dir / "codex_hpepdock_methods.md"
    methods_md.write_text(
        "\n".join([
            "# HPEPDOCK Methods And Validation Notes",
            "",
            "Peptide docking poses were generated with the Huang Lab HPEPDOCK 2.0 web service.",
            "The saved HPEPDOCK peptide-only ranked poses were locally combined with the receptor model used for submission.",
            "For local post-processing, the receptor was held fixed and each peptide pose was subjected to a conservative rigid-body steric relaxation.",
            "The relaxation minimizes a clash penalty between peptide and receptor heavy atoms while tethering the peptide near the submitted HPEPDOCK pose; it is not a full molecular mechanics force-field minimization.",
            "Validation metrics report heavy-atom interface contacts within 4.0 A, clashes within 2.0 A, H-bond-like N/O/S pairs within 3.5 A, salt-bridge side-chain N/O pairs within 4.0 A, peptide rigid-body shift, and contact retention after relaxation.",
            "Recommended poses are ranked by validation status first, then HPEPDOCK ITScore plus geometric penalties for remaining clashes, excessive relaxation shift, low contact retention, and sparse interfaces.",
            "",
            f"Receptor source: `{receptor_pdb_path or 'not found'}`",
        ]) + "\n",
        encoding="utf-8",
    )
    outputs["methods_md"] = methods_md

    manifest_json = case_dir / "codex_hpepdock_manifest.json"
    manifest = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "case_dir": str(case_dir),
        "receptor_pdb": str(receptor_pdb_path or ""),
        "best_rank": best.get("rank") if best else None,
        "best_complex_pdb": best.get("complex_pdb") if best else "",
        "poses": summaries,
    }
    manifest_json.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    outputs["manifest_json"] = manifest_json

    if best and best.get("complex_pdb"):
        best_pose = case_dir / "codex_hpepdock_best_pose.pdb"
        try:
            shutil.copyfile(best["complex_pdb"], best_pose)
            outputs["best_pose_pdb"] = best_pose
        except Exception:
            outputs["best_pose_pdb"] = Path(best["complex_pdb"])
        best_report = case_dir / "codex_hpepdock_best_pose.md"
        best_report.write_text(
            "\n".join([
                "# Recommended HPEPDOCK Pose",
                "",
                f"- validation rank: {best.get('validation_rank')}",
                f"- HPEPDOCK rank: {best.get('rank')}",
                f"- status: {best.get('validation_status')}",
                f"- ITScore: {best.get('itscore')}",
                f"- refined score: {_as_float(best.get('refined_score'), 0.0):.3f}",
                f"- peptide shift: {_as_float(best.get('minimization_translation_a'), 0.0):.3f} A",
                f"- clashes <=2A: {best.get('raw_clash_atom_pairs_2a')} -> {best.get('clash_atom_pairs_2a')}",
                f"- closest heavy-atom distance: {_as_float(best.get('raw_min_distance_a'), 0.0):.2f} -> {_as_float(best.get('min_distance_a'), 0.0):.2f} A",
                f"- contact retention: {_as_float(best.get('contact_retained_fraction'), 0.0):.2f}",
                f"- flags: {best.get('validation_flags')}",
                f"- complex: `{best.get('complex_pdb')}`",
                "",
                "## Top Interface Pairs",
                "",
                str(best.get("top_interface_pairs") or "none"),
            ]) + "\n",
            encoding="utf-8",
        )
        outputs["best_report_md"] = best_report

        receptor_chain = _first_chain_from_summary(best.get("receptor_chains")) or "A"
        peptide_chain = _first_chain_from_summary(best.get("peptide_chains")) or "E"
        view_best = case_dir / "codex_hpepdock_view_best.cxc"
        view_best.write_text(
            "\n".join([
                "close",
                f"open {_cxc_quote(outputs.get('best_pose_pdb', best.get('complex_pdb')))}",
                "hide atoms",
                f"cartoon /{receptor_chain}",
                f"show /{peptide_chain} atoms",
                f"style /{peptide_chain} stick",
                f"color /{receptor_chain} lightgray",
                f"color /{peptide_chain} red",
                "view",
            ]) + "\n",
            encoding="utf-8",
        )
        outputs["view_best_cxc"] = view_best

    view_all = case_dir / "codex_hpepdock_view_all.cxc"
    view_lines = ["close"]
    for item in sorted(summaries, key=lambda row: _as_int(row.get("validation_rank"), 999999)):
        if item.get("complex_pdb"):
            view_lines.append(f"open {_cxc_quote(item['complex_pdb'])}")
    view_lines.extend(["color bychain", "cartoon", "style stick", "view"])
    view_all.write_text("\n".join(view_lines) + "\n", encoding="utf-8")
    outputs["view_all_cxc"] = view_all
    return outputs


def _write_hpepdock_summary(case_dir, summaries, *, receptor_pdb_path=None):
    case_dir = Path(case_dir)
    tsv_path = case_dir / "codex_hpepdock_summary.tsv"
    validation_tsv_path = case_dir / "codex_hpepdock_validation.tsv"
    md_path = case_dir / "codex_hpepdock_summary.md"
    fields = [
        "validation_rank", "validation_status", "recommended", "refined_score", "validation_flags",
        "rank", "pose_pdb", "raw_complex_pdb", "minimized_peptide_pdb", "complex_pdb",
        "itscore", "rmsd", "cluster", "ligand",
        "minimization_status", "minimization_steps", "minimization_translation_a",
        "minimization_energy_initial", "minimization_energy_final",
        "raw_contact_residue_pairs_4a", "raw_clash_atom_pairs_2a", "raw_min_distance_a",
        "contact_retained_fraction",
        "peptide_sequence", "peptide_length", "receptor_chains", "peptide_chains",
        "contact_residue_pairs_4a", "contact_atom_pairs_4a",
        "clash_atom_pairs_2a", "hbond_like_pairs_3_5a", "salt_bridges_4a", "min_distance_a",
        "receptor_interface_residues", "peptide_interface_residues", "top_interface_pairs",
    ]
    rows = []
    for item in summaries:
        row = dict(item)
        for key in ("itscore", "rmsd", "minimization_translation_a", "contact_retained_fraction", "refined_score"):
            if row.get(key) not in (None, ""):
                row[key] = f"{float(row[key]):.3f}"
        for key in ("min_distance_a", "raw_min_distance_a"):
            if row.get(key) not in (None, ""):
                row[key] = f"{float(row[key]):.2f}"
        for key in ("minimization_energy_initial", "minimization_energy_final"):
            if row.get(key) not in (None, ""):
                row[key] = f"{float(row[key]):.4f}"
        rows.append(row)
    for path in (tsv_path, validation_tsv_path):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    lines = [
        "# HPEPDOCK Result Summary",
        "",
        f"- Result folder: `{case_dir}`",
        f"- Receptor used for local complex/context: `{receptor_pdb_path or 'not found'}`",
        "- ITScore is the HPEPDOCK ranking score; lower/more negative ranks better here, but it is not a measured binding free energy.",
        "- Minimized files are receptor-fixed, peptide rigid-body steric relaxations. Use them as clash-reduced docking poses, not as full force-field MM refinements.",
        "- Contacts are local geometric annotations from the saved receptor/peptide coordinates: heavy-atom contacts <= 4.0 A, clashes <= 2.0 A, H-bond-like N/O/S pairs <= 3.5 A, salt-bridge side-chain N/O pairs <= 4.0 A.",
    ]
    best = _best_hpepdock_summary(summaries)
    if best:
        lines.extend([
            f"- Recommended pose: validation rank {best.get('validation_rank')}, HPEPDOCK rank {best.get('rank')}, status {best.get('validation_status')}, file `{best.get('complex_pdb')}`",
            "",
        ])
    lines.extend([
        "| Validation rank | HPE rank | status | ITScore | refined score | shift A | raw clashes | clashes | raw closest | closest | contacts retained | flags |",
        "| ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ])
    ranked_summaries = sorted(summaries, key=lambda row: _as_int(row.get("validation_rank"), 999999))
    for item in ranked_summaries[:20]:
        score = "" if item.get("itscore") is None else f"{float(item['itscore']):.3f}"
        refined = "" if item.get("refined_score") is None else f"{float(item['refined_score']):.3f}"
        closest = "" if item.get("min_distance_a") is None else f"{float(item['min_distance_a']):.2f}"
        raw_closest = "" if item.get("raw_min_distance_a") in (None, "") else f"{float(item['raw_min_distance_a']):.2f}"
        shift = "" if item.get("minimization_translation_a") in (None, "") else f"{float(item['minimization_translation_a']):.3f}"
        retained = "" if item.get("contact_retained_fraction") in (None, "") else f"{float(item['contact_retained_fraction']):.2f}"
        flags = str(item.get("validation_flags") or "").replace("|", "/")
        lines.append(
            f"| {item.get('validation_rank')} | {item.get('rank')} | {item.get('validation_status')} | "
            f"{score} | {refined} | {shift} | "
            f"{item.get('raw_clash_atom_pairs_2a', '')} | "
            f"{item.get('clash_atom_pairs_2a', 0)} | "
            f"{raw_closest} | {closest} | {retained} | {flags} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    outputs = _write_hpepdock_auxiliary_outputs(case_dir, summaries, receptor_pdb_path=receptor_pdb_path)
    outputs.update({
        "summary_tsv": tsv_path,
        "summary_md": md_path,
        "validation_tsv": validation_tsv_path,
    })
    return outputs


def prepare_hpepdock_results(
    output_dir,
    *,
    receptor_pdb_path=None,
    top_n=5,
    minimize=True,
    minimize_steps=240,
    force_minimize=False,
):
    """Create complex PDBs and a richer local summary for HPEPDOCK outputs.

    Returns a dict with `complexes`, `summaries`, and summary file paths. If the
    folder is not an HPEPDOCK result, returns an empty package.
    """
    root = Path(output_dir).expanduser()
    if not root.exists() or not _looks_like_hpepdock_dir(root):
        return {"complexes": [], "summaries": []}
    pose_files = _hpepdock_pose_files(root, top_n=top_n)
    if not pose_files:
        return {"complexes": [], "summaries": []}
    case_dir = pose_files[0].parent
    scores = parse_hpepdock_scores(case_dir)
    receptor = _find_receptor_for_hpepdock(case_dir, receptor_pdb_path=receptor_pdb_path)
    summaries = []
    complexes = []
    for pose in pose_files:
        rank = _rank_from_path(pose)
        score_info = dict(scores.get(rank) or {})
        score_info.setdefault("rank", rank)
        summary = {
            "rank": rank,
            "pose_pdb": str(pose),
            "itscore": score_info.get("itscore"),
            "rmsd": score_info.get("rmsd"),
            "cluster": score_info.get("cluster"),
            "ligand": score_info.get("ligand"),
            "peptide_sequence": score_info.get("peptide_sequence"),
        }
        if receptor is not None:
            complex_path = case_dir / f"rank{rank}_complex.pdb"
            build_hpepdock_complex_pdb(receptor, pose, complex_path, score_info=score_info)
            raw_summary = summarize_hpepdock_pose(receptor, pose, score_info=score_info)
            preferred_pose = pose
            preferred_complex = complex_path
            min_result = {
                "status": "disabled",
                "steps": 0,
                "translation_a": "",
                "energy_initial": "",
                "energy_final": "",
            }
            if minimize:
                minimized_pose = case_dir / f"rank{rank}_minimized_peptide.pdb"
                min_result = minimize_hpepdock_pose(
                    receptor,
                    pose,
                    minimized_pose,
                    max_steps=minimize_steps,
                    force=force_minimize,
                )
                min_score_info = dict(score_info)
                min_score_info.update({
                    "minimization_status": min_result.get("status"),
                    "minimization_translation_a": min_result.get("translation_a"),
                })
                minimized_complex = case_dir / f"rank{rank}_minimized_complex.pdb"
                build_hpepdock_complex_pdb(
                    receptor,
                    minimized_pose,
                    minimized_complex,
                    score_info=min_score_info,
                )
                preferred_pose = minimized_pose
                preferred_complex = minimized_complex
            complexes.append(preferred_complex)
            summary = summarize_hpepdock_pose(receptor, preferred_pose, score_info=score_info)
            summary.update({
                "raw_complex_pdb": str(complex_path),
                "minimized_peptide_pdb": str(preferred_pose if minimize else ""),
                "complex_pdb": str(preferred_complex),
                "minimization_status": min_result.get("status"),
                "minimization_steps": min_result.get("steps"),
                "minimization_translation_a": min_result.get("translation_a"),
                "minimization_energy_initial": min_result.get("energy_initial"),
                "minimization_energy_final": min_result.get("energy_final"),
                "raw_contact_residue_pairs_4a": raw_summary.get("contact_residue_pairs_4a"),
                "raw_clash_atom_pairs_2a": raw_summary.get("clash_atom_pairs_2a"),
                "raw_min_distance_a": raw_summary.get("min_distance_a"),
            })
            raw_contacts = raw_summary.get("contact_residue_pairs_4a") or 0
            if raw_contacts:
                summary["contact_retained_fraction"] = float(summary.get("contact_residue_pairs_4a") or 0) / float(raw_contacts)
            else:
                summary["contact_retained_fraction"] = ""
        else:
            peptide_atoms = _read_pdb_atoms(pose, role="peptide")
            summary.update({
                "validation_status": "reject",
                "validation_flags": "receptor not found",
                "refined_score": "",
                "validation_rank": "",
                "recommended": "no",
                "complex_pdb": "",
                "raw_complex_pdb": "",
                "minimized_peptide_pdb": "",
                "minimization_status": "skipped_no_receptor",
                "minimization_steps": "",
                "minimization_translation_a": "",
                "minimization_energy_initial": "",
                "minimization_energy_final": "",
                "raw_contact_residue_pairs_4a": "",
                "raw_clash_atom_pairs_2a": "",
                "raw_min_distance_a": "",
                "contact_retained_fraction": "",
                "receptor_chains": "not found",
                "peptide_chains": _chain_summary(peptide_atoms),
                "peptide_length": len({_residue_key(atom) for atom in peptide_atoms}),
                "contact_residue_pairs_4a": "",
                "contact_atom_pairs_4a": "",
                "clash_atom_pairs_2a": "",
                "hbond_like_pairs_3_5a": "",
                "salt_bridges_4a": "",
                "min_distance_a": "",
                "receptor_interface_residues": "",
                "peptide_interface_residues": "",
                "top_interface_pairs": "",
            })
        summaries.append(summary)
    _annotate_hpepdock_recommendations(summaries)
    outputs = _write_hpepdock_summary(case_dir, summaries, receptor_pdb_path=receptor)
    return {
        "case_dir": case_dir,
        "receptor": receptor,
        "complexes": complexes,
        "summaries": summaries,
        **outputs,
    }


def run_hpepdock_pipeline(
    session,
    receptor_pdb_path,
    peptide_sequence,
    email,
    output_dir,
    *,
    top_n=5,
    poll_interval=30,
    max_wait=3600,
    jobname=None,
    site_residues=None,
):
    """End-to-end blocking call: submit → poll → download → extract.

    Designed to be called from a worker thread (use the existing
    `_start_rapidock_output_watcher` plus a thread, exactly like the native
    path). Logs progress through session.logger.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def log(msg, level="info"):
        if session is None:
            return
        try:
            getattr(session.logger, level)(f"[HPEPDOCK] {msg}")
        except Exception:
            pass
        try:
            session.logger.status(f"[HPEPDOCK] {msg}")
        except Exception:
            pass

    log(f"Submitting receptor + peptide ({len(peptide_sequence)} aa) to HuangLab HPEPDOCK …")
    job_id = submit_hpepdock(
        receptor_pdb_path,
        peptide_sequence,
        email,
        jobname=jobname,
        site_residues=site_residues,
    )
    log(f"Job ID: {job_id}. Status page: {status_url(job_id)}")
    deadline = time.time() + max_wait
    last_state = None
    last_state_change = time.time()
    last_heartbeat = 0
    while time.time() < deadline:
        # Direct artifact probe is the source of truth — HuangLab page HTML
        # has shifted between releases and the token list goes stale fast.
        # Pass top_n so we don't break early before all requested ranks are written.
        if result_ready(job_id, top_n=top_n):
            state = "finished"
            log(f"Job complete (model_1.pdb..model_{top_n}.pdb available) — downloading poses …")
            break
        try:
            html = fetch_status(job_id)
        except HpepdockError as err:
            log(f"Status check failed: {err}", level="warning")
            time.sleep(poll_interval)
            continue
        state = parse_job_state(html)
        if state == "finished":
            log("Job complete — downloading poses …")
            break
        if state != last_state:
            elapsed_min = int((time.time() - deadline + max_wait) / 60)
            log(f"State: {state.upper()} (elapsed ~{elapsed_min} min)")
            last_state = state
            last_state_change = time.time()
            last_heartbeat = time.time()
        elif time.time() - last_heartbeat > 120:
            stuck_min = int((time.time() - last_state_change) / 60)
            log(f"Still {state.upper()} for ~{stuck_min} min. HuangLab queue can fluctuate; will keep polling.")
            last_heartbeat = time.time()
        time.sleep(poll_interval)
    else:
        raise HpepdockError(
            f"HPEPDOCK job {job_id} did not complete within {max_wait}s "
            f"(last state: {last_state}). Open {status_url(job_id)} to check manually."
        )

    log(f"Downloading top {top_n} pose PDBs directly (no tarball extraction).")
    try:
        written = download_individual_models(job_id, output_dir, top_n=top_n)
    except HpepdockError as err:
        log(f"Per-model download failed ({err}); trying tarball fallback.", level="warning")
        tarball = output_dir / "hpepdock_top.tar.gz"
        download_result_tarball(job_id, tarball)
        written = extract_results(tarball, output_dir, top_n=top_n)

    tarball = output_dir / "hpepdock_top.tar.gz"
    try:
        if not tarball.exists() or tarball.stat().st_size < 5_000:
            download_result_tarball(job_id, tarball)
        metadata = extract_hpepdock_metadata(tarball, output_dir)
        if metadata:
            log(f"Extracted HPEPDOCK score/metadata files: {[p.name for p in metadata[:5]]}")
    except Exception as err:
        log(f"Could not fetch HPEPDOCK metadata tarball; poses are still usable ({err}).", level="warning")

    try:
        package = prepare_hpepdock_results(
            output_dir,
            receptor_pdb_path=receptor_pdb_path,
            top_n=top_n,
        )
        if package.get("complexes"):
            best = next((item for item in package.get("summaries", []) if item.get("recommended") == "yes"), None)
            best_text = ""
            if best:
                best_text = f" Recommended HPE rank {best.get('rank')} ({best.get('validation_status')})."
            log(
                f"Built/minimized {len(package['complexes'])} receptor+peptide complex PDB(s), "
                f"wrote {Path(package['summary_tsv']).name} and {Path(package['validation_tsv']).name}."
                f"{best_text}"
            )
    except Exception as err:
        log(f"Could not build HPEPDOCK complex summary package: {err}", level="warning")

    # Critic P6: signal the watcher that all poses are fully written before it loads.
    try:
        (output_dir / ".codex_rapidock_done").write_text(time.strftime("%Y-%m-%d %H:%M:%S\n"))
    except Exception:
        pass
    log(f"Wrote {len(written)} ranked pose(s): {[p.name for p in written]}")
    return {
        "job_id": job_id,
        "status_url": status_url(job_id),
        "poses": written,
    }
