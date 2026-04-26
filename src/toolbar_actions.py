import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import tempfile
from urllib.parse import quote
import webbrowser

CONSURF_COLAB_URL = "https://colab.research.google.com/drive/1PhDXX7k12oUsV6T_xkXC3Rm9R99e7tHz"
HHPRED_URL = "https://toolkit.tuebingen.mpg.de/tools/hhpred"
DALI_URL = "https://ekhidna2.biocenter.helsinki.fi/dali/"
VAST_URL = "https://www.ncbi.nlm.nih.gov/Structure/VAST/vastsearch.html"
PDBEFOLD_URL = "https://www.ebi.ac.uk/msd-srv/ssm/"
USALIGN_URL = "https://aideepmed.com/US-align/"
PISA_URL = "https://www.ebi.ac.uk/pdbe/prot_int/"
CAVER_URL = "https://loschmidt.chemi.muni.cz/caverweb/"


def _show_ai_prompt(session, prompt):
    from .tool import CodexAssistant

    assistant = CodexAssistant.get_singleton(session)
    if assistant is None:
        return
    assistant.display(True)
    assistant._show_assistant_tab()
    try:
        if not assistant.prompt_edit.isReadOnly():
            assistant.prompt_edit.setPlainText(prompt)
    except Exception:
        pass
    assistant._focus_prompt()


def _report_toolbar_result(session, message, *, error=False):
    if not message:
        return
    def emit():
        try:
            if error:
                session.logger.error(message)
            else:
                session.logger.info(message)
        except Exception:
            pass
    try:
        session.ui.thread_safe(emit)
    except Exception:
        emit()


def _run_toolbar_task(session, label, fn):
    _report_toolbar_result(session, f"{label}: starting...")

    def worker():
        try:
            message = fn()
        except Exception as err:
            _report_toolbar_result(session, f"{label} failed: {err}", error=True)
            return
        _report_toolbar_result(session, message or f"{label}: done")

    threading.Thread(target=worker, daemon=True).start()


def _run_chimerax_thread_safe(session, command):
    if _is_qt_main_thread():
        from chimerax.core.commands import run

        return run(session, command)

    result_box = {}
    event = threading.Event()

    def runner():
        try:
            from chimerax.core.commands import run

            result_box["result"] = run(session, command)
        except Exception as err:
            result_box["error"] = err
        finally:
            event.set()

    try:
        session.ui.thread_safe(runner)
    except Exception:
        runner()
    event.wait()
    if "error" in result_box:
        raise result_box["error"]
    return result_box.get("result")


def _is_qt_main_thread():
    try:
        from Qt.QtCore import QCoreApplication, QThread

        app = QCoreApplication.instance()
        return app is not None and QThread.currentThread() == app.thread()
    except Exception:
        return False


def _run_toolbar_chimerax_task(session, label, fn):
    _run_toolbar_task(
        session,
        label,
        lambda: fn(lambda command: _run_chimerax_thread_safe(session, command)),
    )


def _start_native_tool(session, tool_name):
    try:
        session.tools.start_tools([tool_name])
    except Exception:
        session.logger.report_exception(preface=f'Tool "{tool_name}" failed to start')


def _tool_is_displayed(tool):
    try:
        return bool(tool.displayed())
    except Exception:
        pass
    dock_widget = getattr(getattr(tool, "tool_window", None), "_dock_widget", None)
    if dock_widget is not None:
        try:
            return not dock_widget.isHidden()
        except Exception:
            pass
    return False


def _toggle_singleton_tool(session, tool_class, label):
    tool = tool_class.get_singleton(session, create=False, display=False)
    if tool is not None and _tool_is_displayed(tool):
        tool.display(False)
        _report_toolbar_result(session, f"{label}: hidden")
        return tool, False

    tool = tool_class.get_singleton(session, create=True, display=True)
    if tool is not None:
        tool.display(True)
    _report_toolbar_result(session, f"{label}: opened")
    return tool, True


def _result_failed_to_resolve(result_text):
    text = str(result_text or "").lower()
    return text.startswith("no ") or "no alignment is open" in text


def _protein_chain_specs(session):
    from .semantic import get_session_semantics, resolve_default_model_spec

    semantics = get_session_semantics(session)
    selection = semantics.get("selection", {})
    chains = []

    for token in selection.get("ranges", []):
        head = str(token).split(":", 1)[0]
        if "/" in head and head not in chains:
            chains.append(head)

    if chains:
        return chains

    target_models = list(selection.get("models", []))
    if not target_models:
        default_model = resolve_default_model_spec(session)
        if default_model:
            target_models = [default_model]

    for model in semantics.get("models", []):
        if model.get("spec") not in target_models or not model.get("atomic"):
            continue
        for chain in model.get("chains", []):
            if chain.get("polymer_type") != "protein":
                continue
            spec = f"{model['spec']}/{chain['id']}"
            if spec not in chains:
                chains.append(spec)
    return chains


def _default_model_spec(session):
    from .semantic import resolve_default_model_spec

    return resolve_default_model_spec(session)


def _protein_chain_entries(session):
    from chimerax.atomic import ChainArg

    entries = []
    for spec in _protein_chain_specs(session):
        try:
            chain = ChainArg.parse(spec, session)[0]
        except Exception:
            continue
        try:
            sequence = chain.ungapped()
        except Exception:
            sequence = ""
        if not sequence:
            continue
        entries.append(
            {
                "spec": spec,
                "chain_id": getattr(chain, "chain_id", ""),
                "structure_name": getattr(getattr(chain, "structure", None), "name", "structure"),
                "sequence": sequence,
            }
        )
    return entries


def _fasta_text(entries):
    blocks = []
    for entry in entries:
        header = f">{entry['structure_name']}_{entry['chain_id']}|{entry['spec']}"
        blocks.append(header + "\n" + entry["sequence"])
    return "\n".join(blocks).strip() + ("\n" if blocks else "")


def _sequence_text_from_fasta(fasta):
    return "".join(
        line.strip()
        for line in str(fasta or "").splitlines()
        if line.strip() and not line.startswith(">")
    )


def _copy_text_to_clipboard(text):
    if not text:
        return
    if sys.platform == "darwin":
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)


def _sequence_launcher_script():
    return Path(__file__).resolve().with_name("web_sequence_launcher.mjs")


def _launch_with_browser_helper(sites, fasta="", **extra):
    script = _sequence_launcher_script()
    if not script.exists():
        return None
    payload = {"sites": sites, "fasta": fasta}
    payload.update(extra)
    try:
        result = subprocess.run(
            ["node", str(script)],
            input=json.dumps(payload).encode("utf-8"),
            capture_output=True,
            timeout=60,
            check=False,
        )
    except Exception as err:
        return f"Browser helper launch failed: {err}"
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        if not detail:
            detail = result.stdout.decode("utf-8", errors="replace").strip()
        return f"Browser helper launch failed: {detail or f'exit {result.returncode}'}"
    return None


def _quote_command_token(text):
    from chimerax.core.commands import StringArg

    return StringArg.unparse(str(text))


def _run_safari_prefill(url, js_code, delays=(3, 6, 10)):
    if sys.platform != "darwin":
        webbrowser.open(url)
        return True, ""
    lines = [
        'tell application "Safari"',
        "activate",
        f"open location {json.dumps(url)}",
    ]
    for delay in delays:
        lines.extend(
            [
                f"delay {delay}",
                f"do JavaScript {json.dumps(js_code)} in front document",
            ]
        )
    lines.append("end tell")
    cmd = ["osascript"]
    for line in lines:
        cmd.extend(["-e", line])
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    combined = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if result.returncode != 0 and "Allow JavaScript from Apple Events" in combined:
        return False, "Safari setting 'Allow JavaScript from Apple Events' is disabled."
    return result.returncode == 0, combined


def _run_safari_tab_batch(tab_specs):
    if sys.platform != "darwin":
        for spec in tab_specs:
            webbrowser.open_new_tab(spec["url"])
        return True, ""

    if not tab_specs:
        return True, ""

    first_url = tab_specs[0]["url"]
    lines = ['tell application "Safari"', "activate", f"make new document with properties {{URL:{json.dumps(first_url)}}}"]

    for spec in tab_specs[1:]:
        lines.append(
            f"tell front window to set current tab to (make new tab with properties {{URL:{json.dumps(spec['url'])}}})"
        )

    for delay in (4, 8, 12):
        lines.append(f"delay {delay}")
        for index, spec in enumerate(tab_specs, start=1):
            js_code = spec.get("js")
            if not js_code:
                continue
            lines.append(
                f"set current tab of front window to tab {index} of front window"
            )
            lines.append(f"do JavaScript {json.dumps(js_code)} in current tab of front window")

    lines.append("end tell")
    cmd = ["osascript"]
    for line in lines:
        cmd.extend(["-e", line])
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    combined = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if result.returncode != 0 and "Allow JavaScript from Apple Events" in combined:
        return False, "Safari setting 'Allow JavaScript from Apple Events' is disabled."
    return result.returncode == 0, combined


def _generic_sequence_fill_js(sequence_text):
    return f"""
(() => {{
  const value = {json.dumps(sequence_text)};
  const matches = (el) => {{
    const placeholder = (el.placeholder || '').toLowerCase();
    const aria = (el.getAttribute('aria-label') || '').toLowerCase();
    const name = (el.name || '').toLowerCase();
    const id = (el.id || '').toLowerCase();
    return (
      placeholder.includes('sequence') ||
      placeholder.includes('fasta') ||
      placeholder.includes('protein') ||
      aria.includes('sequence') ||
      aria.includes('fasta') ||
      name.includes('sequence') ||
      name.includes('query') ||
      id.includes('sequence') ||
      id.includes('query')
    );
  }};
  const visible = (el) => {{
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }};
  const setValue = (target) => {{
    target.scrollIntoView({{ block: 'center', inline: 'center' }});
    target.focus();
    if (target.isContentEditable) {{
      target.textContent = value;
    }} else {{
      const proto = target.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
      if (descriptor && descriptor.set) descriptor.set.call(target, value);
      else target.value = value;
    }}
    try {{
      target.dispatchEvent(new InputEvent('input', {{ bubbles: true, inputType: 'insertText', data: value }}));
    }} catch (_) {{
      target.dispatchEvent(new Event('input', {{ bubbles: true }}));
    }}
    target.dispatchEvent(new Event('change', {{ bubbles: true }}));
    target.focus();
    const current = target.isContentEditable ? target.textContent : target.value;
    return !!(current && current.length >= Math.min(20, value.length));
  }};
  const apply = () => {{
    const candidates = Array.from(document.querySelectorAll('textarea,input[type="text"],input:not([type]),[contenteditable="true"]'))
      .filter(visible);
    const target = candidates.find(matches) || candidates.find(el => el.tagName === 'TEXTAREA') || candidates[0];
    if (!target) return false;
    return setValue(target);
  }};
  if (apply()) return 'filled';
  let attempts = 0;
  const timer = setInterval(() => {{
    attempts += 1;
    if (apply() || attempts > 60) clearInterval(timer);
  }}, 500);
  return 'waiting';
}})();
""".strip()


def _uniprot_fill_js(sequence_text):
    return f"""
(() => {{
  const value = {json.dumps(sequence_text)};
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }};
  const score = (el) => {{
    const text = [
      el.getAttribute('aria-label') || '',
      el.getAttribute('placeholder') || '',
      el.getAttribute('name') || '',
      el.getAttribute('id') || '',
      el.closest('label')?.textContent || '',
      el.closest('form')?.textContent || '',
      el.parentElement?.textContent || ''
    ].join(' ').toLowerCase();
    let s = el.tagName === 'TEXTAREA' ? 20 : 0;
    if (text.includes('blast')) s += 14;
    if (text.includes('protein')) s += 12;
    if (text.includes('sequence')) s += 10;
    if (text.includes('fasta')) s += 10;
    if (text.includes('query')) s += 5;
    if (text.includes('search') && el.tagName !== 'TEXTAREA') s -= 8;
    if (el.disabled || el.readOnly) s -= 20;
    return s;
  }};
  const setValue = (target) => {{
    target.scrollIntoView({{ block: 'center', inline: 'center' }});
    target.focus();
    if (target.isContentEditable) {{
      target.textContent = value;
    }} else {{
      const proto = target.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
      if (target._valueTracker) target._valueTracker.setValue('');
      if (descriptor && descriptor.set) descriptor.set.call(target, value);
      else target.value = value;
    }}
    try {{
      target.dispatchEvent(new InputEvent('beforeinput', {{ bubbles: true, inputType: 'insertText', data: value }}));
      target.dispatchEvent(new InputEvent('input', {{ bubbles: true, inputType: 'insertText', data: value }}));
    }} catch (_) {{
      target.dispatchEvent(new Event('input', {{ bubbles: true }}));
    }}
    target.dispatchEvent(new Event('change', {{ bubbles: true }}));
    target.dispatchEvent(new KeyboardEvent('keyup', {{ bubbles: true, key: 'A' }}));
    target.dispatchEvent(new Event('blur', {{ bubbles: true }}));
    target.focus();
    const current = target.isContentEditable ? target.textContent : target.value;
    return !!(current && current.length >= Math.min(20, value.length));
  }};
  const apply = () => {{
    const candidates = [
      ...document.querySelectorAll('textarea[name*="sequence" i], textarea[id*="sequence" i], textarea[placeholder*="sequence" i], textarea[placeholder*="protein" i], textarea[placeholder*="fasta" i], textarea[aria-label*="sequence" i], textarea[aria-label*="protein" i], textarea'),
      ...document.querySelectorAll('input[name*="sequence" i], input[id*="sequence" i], input[placeholder*="sequence" i], input[placeholder*="protein" i], input[placeholder*="fasta" i], input[aria-label*="sequence" i], input[aria-label*="protein" i], input[type="text"], input:not([type])'),
      ...document.querySelectorAll('[contenteditable="true"]')
    ].filter(isVisible).sort((a, b) => score(b) - score(a));
    const target = candidates[0];
    if (!target) return false;
    return setValue(target);
  }};
  if (apply()) return 'filled';
  let attempts = 0;
  const timer = setInterval(() => {{
    attempts += 1;
    if (apply() || attempts > 80) clearInterval(timer);
  }}, 500);
  return 'waiting';
}})();
""".strip()


def _hmmer_fill_js(sequence_text):
    return f"""
(() => {{
  const value = {json.dumps(sequence_text)};
  const apply = () => {{
    const candidates = Array.from(document.querySelectorAll('textarea,input[type="text"],input:not([type])')).filter(
      el => !!(el.offsetParent !== null)
    );
    const target = candidates.find(el => {{
      const p = (el.placeholder || '').toLowerCase();
      const a = (el.getAttribute('aria-label') || '').toLowerCase();
      const n = (el.name || '').toLowerCase();
      return p.includes('sequence') || a.includes('sequence') || n.includes('seq');
    }}) || candidates[0];
    if (!target) return false;
    const proto = target.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
    if (!descriptor || !descriptor.set) return false;
    descriptor.set.call(target, value);
    target.dispatchEvent(new Event('input', {{ bubbles: true }}));
    target.dispatchEvent(new Event('change', {{ bubbles: true }}));
    target.focus();
    return true;
  }};
  if (apply()) return 'filled';
  let attempts = 0;
  const timer = setInterval(() => {{
    attempts += 1;
    if (apply() || attempts > 80) clearInterval(timer);
  }}, 500);
  return 'waiting';
}})();
""".strip()


def _interpro_fill_js(sequence_text):
    return f"""
(() => {{
  const value = {json.dumps(sequence_text)};
  const apply = () => {{
    const candidates = Array.from(document.querySelectorAll('textarea,input[type="text"],input:not([type])')).filter(
      el => !!(el.offsetParent !== null)
    );
    const target = candidates.find(el => {{
      const p = (el.placeholder || '').toLowerCase();
      const a = (el.getAttribute('aria-label') || '').toLowerCase();
      const n = (el.name || '').toLowerCase();
      return p.includes('sequence') || p.includes('fasta') || a.includes('sequence') || n.includes('sequence');
    }}) || candidates[0];
    if (!target) return false;
    const proto = target.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
    if (!descriptor || !descriptor.set) return false;
    descriptor.set.call(target, value);
    target.dispatchEvent(new Event('input', {{ bubbles: true }}));
    target.dispatchEvent(new Event('change', {{ bubbles: true }}));
    target.focus();
    return true;
  }};
  if (apply()) return 'filled';
  let attempts = 0;
  const timer = setInterval(() => {{
    attempts += 1;
    if (apply() || attempts > 80) clearInterval(timer);
  }}, 500);
  return 'waiting';
}})();
""".strip()


def _launch_uniprot_blast_page(session):
    entries = _protein_chain_entries(session)
    if not entries:
        return "No protein chain sequence was resolved for UniProt BLAST."
    fasta = _fasta_text(entries)
    _copy_text_to_clipboard(fasta)
    helper_error = _launch_with_browser_helper(["uniprot"], fasta)
    if helper_error is None:
        return f"Opened UniProt BLAST and filled {len(entries)} sequence(s) in Chrome."
    js_code = _uniprot_fill_js(fasta)
    ok, detail = _run_safari_prefill("https://www.uniprot.org/blast", js_code)
    if ok:
        return f"Opened UniProt BLAST and copied {len(entries)} sequence(s) in FASTA format."
    return (
        f"Opened UniProt BLAST and copied {len(entries)} sequence(s) in FASTA format, "
        f"but automatic page filling failed: {detail}"
    )


def _sequence_site_choices(session):
    defaults = {
        "uniprot": True,
        "ncbi": True,
        "consurf": False,
        "hhpred": False,
        "rcsb": False,
        "hmmer": False,
        "interpro": False,
    }
    stored = getattr(session, "_codex_bridge_sequence_site_choices", None)
    if isinstance(stored, dict):
        merged = defaults.copy()
        merged.update({k: bool(v) for k, v in stored.items() if k in defaults})
        return merged
    return defaults


def _choose_sequence_analysis_sites(session):
    if not session.ui.is_gui:
        return ["uniprot"]

    from Qt.QtGui import QCursor
    from Qt.QtWidgets import QMenu

    menu = QMenu(session.ui.main_window)
    options = [
        ("uniprot", "UniProt BLAST"),
        ("ncbi", "NCBI BLASTP"),
        ("hhpred", "HHpred / HHblits"),
        ("consurf", "ConSurf Colab"),
        ("rcsb", "RCSB sequence search"),
        ("hmmer", "HMMER phmmer"),
        ("interpro", "InterPro / Pfam"),
    ]
    chosen = {"site": None}
    for key, label in options:
        action = menu.addAction(label)
        action.triggered.connect(lambda _checked=False, site=key: chosen.__setitem__("site", site))

    menu.exec(QCursor.pos())
    return [chosen["site"]] if chosen["site"] else []


def _launch_sequence_analysis_tabs(session, chosen_sites=None, entries=None):
    entries = list(entries) if entries is not None else _protein_chain_entries(session)
    if not entries:
        return "No protein chain sequence was resolved for sequence-analysis tabs."

    primary = entries[0]
    fasta = _fasta_text([primary])
    _copy_text_to_clipboard(fasta)

    if chosen_sites is None:
        chosen_sites = _choose_sequence_analysis_sites(session)
    if not chosen_sites:
        return "No sequence-analysis sites were selected."

    site_defs = {
        "uniprot": {
            "label": "UniProt BLAST",
            "url": "https://www.uniprot.org/blast",
            "js": _uniprot_fill_js(fasta),
        },
        "ncbi": {
            "label": "NCBI BLASTP",
            "url": "https://blast.ncbi.nlm.nih.gov/Blast.cgi?PAGE_TYPE=BlastSearch&PROGRAM=blastp&QUERY="
            + quote(fasta, safe=""),
            "js": None,
        },
        "consurf": {
            "label": "ConSurf Colab",
            "url": CONSURF_COLAB_URL,
            "js": _generic_sequence_fill_js(fasta),
        },
        "hhpred": {
            "label": "HHpred / HHblits",
            "url": HHPRED_URL,
            "js": _generic_sequence_fill_js(fasta),
        },
        "hmmer": {
            "label": "HMMER phmmer",
            "url": "https://www.ebi.ac.uk/Tools/hmmer/search/phmmer",
            "js": _hmmer_fill_js(fasta),
        },
        "rcsb": {
            "label": "RCSB sequence search",
            "url": "https://www.rcsb.org",
            "js": _generic_sequence_fill_js(_sequence_text_from_fasta(fasta)),
        },
        "interpro": {
            "label": "InterPro / Pfam",
            "url": "https://www.ebi.ac.uk/interpro/search/sequence/",
            "js": _interpro_fill_js(fasta),
        },
    }
    tab_specs = [site_defs[key] for key in chosen_sites if key in site_defs]
    if not tab_specs:
        return "No valid sequence-analysis sites were selected."
    helper_error = _launch_with_browser_helper(chosen_sites, fasta)
    if helper_error is None:
        labels = ", ".join(spec["label"] for spec in tab_specs)
        return f"Opened {labels} for {primary['spec']} and filled the sequence in Chrome."
    ok, detail = _run_safari_tab_batch(tab_specs)
    labels = ", ".join(spec["label"] for spec in tab_specs)
    if ok:
        return f"Opened {labels} for {primary['spec']}. FASTA was copied to the clipboard."
    return f"Opened {labels} for {primary['spec']}. FASTA was copied to the clipboard, but automatic page filling failed: {detail}"


def launch_sequence_analysis_site(session, site, entries=None):
    site = str(site or "").strip().lower()
    if site == "uniprot":
        return _launch_uniprot_blast_page(session)
    if site == "ncbi":
        return _launch_ncbi_page(session)
    if site == "hmmer":
        return _launch_hmmer_page(session)
    if site == "interpro":
        return _launch_interpro_page(session)
    if site == "hhpred":
        return _launch_hhpred_page(session)
    if site == "rcsb":
        return _launch_sequence_analysis_tabs(session, ["rcsb"], entries=entries)
    if site == "alphafold":
        return _launch_alphafold_server(session)
    if site == "consurf":
        return _launch_consurf_page(session)
    return f"Unknown sequence-analysis site: {site}"


def _safe_file_stem(text, fallback="model"):
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or "").strip()).strip("._")
    return stem or fallback


def _atomic_model_entries(session, *, selected_preferred=False):
    from chimerax.atomic import AtomicStructure

    selected_ids = set()
    if selected_preferred:
        try:
            selected_ids = {getattr(model, "id_string", "") for model in session.selection.models()}
        except Exception:
            selected_ids = set()
        if not selected_ids:
            try:
                from chimerax.atomic import selected_residues

                for structure, _chain_id, _residues in selected_residues(session).by_chain:
                    selected_ids.add(getattr(structure, "id_string", ""))
            except Exception:
                pass

    entries = []
    for model in session.models.list():
        if not isinstance(model, AtomicStructure):
            continue
        model_id = getattr(model, "id_string", "")
        if selected_ids and model_id not in selected_ids:
            continue
        entries.append(
            {
                "model": model,
                "spec": f"#{model_id}",
                "name": getattr(model, "name", "model") or "model",
            }
        )
    if selected_ids and entries:
        return entries
    if selected_preferred:
        return _atomic_model_entries(session, selected_preferred=False)
    return entries


def _run_chimerax(session, command, executor=None):
    if executor is not None:
        return executor(command)
    from chimerax.core.commands import run

    return run(session, command)


def _save_structure_file(session, model_spec, path, *, fmt="mmcif", executor=None):
    from chimerax.core.commands import StringArg

    quoted = StringArg.unparse(str(path))
    command = f"save {quoted} format {fmt} models {model_spec}"
    _run_chimerax(session, command, executor=executor)
    return command


def _export_model_entries(session, entries, *, fmt="mmcif", prefix="chimerax_structures", executor=None):
    if not entries:
        return [], None
    out_dir = Path(tempfile.mkdtemp(prefix=f"{prefix}_"))
    files = []
    suffix = ".cif" if fmt.lower() in {"mmcif", "cif"} else ".pdb"
    used = set()
    for index, entry in enumerate(entries, start=1):
        stem = _safe_file_stem(f"{entry['spec']}_{entry['name']}", fallback=f"model_{index}")
        while stem in used:
            stem = f"{stem}_{index}"
        used.add(stem)
        path = out_dir / f"{stem}{suffix}"
        _save_structure_file(session, entry["spec"], path, fmt=fmt, executor=executor)
        if path.exists():
            files.append(path)
    return files, out_dir


def _export_model_files(session, *, fmt="mmcif", selected_preferred=False, prefix="chimerax_structures", executor=None):
    entries = _atomic_model_entries(session, selected_preferred=selected_preferred)
    return _export_model_entries(
        session,
        entries,
        fmt=fmt,
        prefix=prefix,
        executor=executor,
    )


def _export_first_structure_file(session, *, fmt="pdb", prefix="chimerax_structure", executor=None):
    files, out_dir = _export_model_files(
        session,
        fmt=fmt,
        selected_preferred=True,
        prefix=prefix,
        executor=executor,
    )
    if not files:
        return None, out_dir
    return files[0], out_dir


def _open_path(path):
    try:
        webbrowser.open(Path(path).resolve().as_uri())
    except Exception:
        webbrowser.open(str(path))


def _run_local_foldmason(files, out_dir):
    executable = shutil.which("foldmason")
    if not executable:
        return None
    result_prefix = Path(out_dir) / "foldmason_result"
    tmp_dir = Path(out_dir) / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    command = [
        executable,
        "easy-msa",
        *[str(path) for path in files],
        str(result_prefix),
        str(tmp_dir),
        "--report-mode",
        "1",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, detail or f"foldmason exited with code {result.returncode}", command
    html = Path(str(result_prefix) + ".html")
    if html.exists():
        _open_path(html)
    return True, str(html if html.exists() else result_prefix), command


def _launch_foldmason_with_files(session, files, out_dir, *, auto_report=""):
    if len(files) < 2:
        detail = " FoldMason is a structure-MSA tool, so it still needs at least two structures."
        if auto_report:
            detail += "\nSimilar auto-search result:\n" + auto_report
        return "FoldMason could not assemble enough structures for comparison." + detail

    local = _run_local_foldmason(files, out_dir)
    if local is not None:
        ok, detail, command = local
        if ok:
            prefix = "Auto-opened Foldseek hits first. " if auto_report else ""
            return f"{prefix}FoldMason local structure MSA/tree finished for {len(files)} structures. Report: {detail}"
        session.logger.warning(f"Local FoldMason failed; falling back to webserver: {detail}")

    file_text = "\n".join(str(path) for path in files)
    _copy_text_to_clipboard(file_text)
    helper_error = _launch_with_browser_helper(
        ["foldmason"],
        "",
        structureFiles=[str(path) for path in files],
    )
    if helper_error is None:
        prefix = "Auto-opened Foldseek hits first. " if auto_report else ""
        return f"{prefix}Opened FoldMason webserver and uploaded {len(files)} exported structure file(s). Export folder: {out_dir}"
    webbrowser.open("https://search.foldseek.com/foldmason")
    prefix = "Auto-opened Foldseek hits first. " if auto_report else ""
    return (
        f"{prefix}Opened FoldMason webserver and exported {len(files)} structure file(s) to {out_dir}. "
        f"File paths were copied to the clipboard. Automatic upload failed: {helper_error}"
    )


def launch_foldmason(session, *, executor=None, auto_similar=True, similar_count=5):
    entries = _atomic_model_entries(session, selected_preferred=True)
    if len(entries) < 2:
        all_entries = _atomic_model_entries(session, selected_preferred=False)
        if len(all_entries) >= 2:
            entries = all_entries
    if len(entries) < 2 and auto_similar:
        return launch_foldseek_foldmason(session, count=similar_count, executor=executor)

    files, out_dir = _export_model_entries(
        session,
        entries,
        fmt="mmcif",
        prefix="chimerax_foldmason",
        executor=executor,
    )
    return _launch_foldmason_with_files(session, files, out_dir)


def _selected_folddisco_motif(session):
    from chimerax.atomic import selected_residues

    selected = selected_residues(session)
    grouped = []
    for structure, chain_id, residues in selected.by_chain:
        items = []
        try:
            residue_iter = list(residues)
        except Exception:
            residue_iter = []
        for residue in residue_iter:
            try:
                number = int(getattr(residue, "number"))
            except Exception:
                continue
            chain = str(getattr(residue, "chain_id", chain_id) or chain_id or "").strip()
            if not chain or chain == "?":
                token = str(number)
            else:
                token = f"{chain}{number}"
            items.append((chain, number, token))
        if items:
            items.sort(key=lambda item: (item[0], item[1]))
            grouped.append((structure, items))
    if not grouped:
        return None
    grouped.sort(key=lambda item: len(item[1]), reverse=True)
    structure, items = grouped[0]
    motif = ",".join(item[2] for item in items)
    return structure, motif, len(items)


def launch_folddisco(session, *, executor=None):
    payload = _selected_folddisco_motif(session)
    if payload is None:
        return "Select one or more motif residues first, then run FoldDisco. The top sequence bar click selection works for this."
    structure, motif, count = payload
    model_spec = f"#{getattr(structure, 'id_string', '?')}"
    out_dir = Path(tempfile.mkdtemp(prefix="chimerax_folddisco_"))
    query_file = out_dir / f"{_safe_file_stem(model_spec, 'query')}_query.cif"
    _save_structure_file(session, model_spec, query_file, fmt="mmcif", executor=executor)
    _copy_text_to_clipboard(motif)
    helper_error = _launch_with_browser_helper(
        ["folddisco"],
        "",
        structureFiles=[str(query_file)],
        motif=motif,
    )
    if helper_error is None:
        return f"Opened FoldDisco webserver for {model_spec} and filled motif residues: {motif}"
    webbrowser.open("https://search.foldseek.com/folddisco")
    return (
        f"Opened FoldDisco webserver. Exported query structure to {query_file}; "
        f"copied motif residues to clipboard: {motif}. Automatic upload/fill failed: {helper_error}"
    )


def _infer_nucleotide_type(sequence, requested=None):
    text = str(requested or "").lower()
    compact = re.sub(r"[^A-Za-z]", "", str(sequence or "")).upper()
    is_rna = "U" in compact and "T" not in compact
    if "ssrna" in text or "single rna" in text:
        return "ssRNA"
    if "dsrna" in text or "duplex rna" in text:
        return "dsRNA"
    if "ssdna" in text or "single dna" in text:
        return "ssDNA"
    if "dsdna" in text or "duplex dna" in text:
        return "dsDNA"
    if is_rna:
        return "ssRNA"
    return "dsDNA"


def _prompt_nucleotide_sequence(session):
    if not getattr(session.ui, "is_gui", False):
        return None
    from Qt.QtWidgets import QInputDialog

    text, ok = QInputDialog.getMultiLineText(
        session.ui.main_window,
        "Nucleotide Docking",
        "Paste DNA/RNA sequence. Add ssDNA, dsDNA, ssRNA, or dsRNA in the first line if needed.",
        "",
    )
    if not ok:
        return None
    return text


def _parse_nucleotide_request(text):
    raw = str(text or "").strip()
    if not raw:
        return "", None
    first_line = raw.splitlines()[0].strip().lower()
    requested = first_line if any(token in first_line for token in ("ssdna", "dsdna", "ssrna", "dsrna")) else None
    sequence = "".join(
        line.strip()
        for line in raw.splitlines()
        if line.strip() and not line.strip().startswith(">") and line.strip().lower() != requested
    )
    sequence = re.sub(r"[^A-Za-z]", "", sequence).upper()
    return sequence, requested


def launch_nucleotide_docking_pipeline(session, request_text=None, *, executor=None):
    if not str(request_text or "").strip():
        request_text = _prompt_nucleotide_sequence(session)
    sequence, requested = _parse_nucleotide_request(request_text)
    if not sequence:
        return "No nucleotide sequence was provided."
    invalid = sorted(set(sequence) - set("ACGTU"))
    if invalid:
        return f"Nucleotide sequence contains unsupported letters: {''.join(invalid)}"

    files, out_dir = _export_model_files(
        session,
        fmt="pdb",
        selected_preferred=True,
        prefix="chimerax_nucdock",
        executor=executor,
    )
    if not files:
        return "No receptor atomic structure is open for nucleotide docking."
    receptor_file = files[0]
    nucleotide_type = _infer_nucleotide_type(sequence, requested=requested)
    fasta = f">nucleotide_query|{nucleotide_type}\n{sequence}\n"
    _copy_text_to_clipboard(fasta)
    helper_error = _launch_with_browser_helper(
        ["hdock-nucleotide"],
        fasta,
        structureFiles=[str(receptor_file)],
        nucleotideSequence=sequence,
        nucleotideType=nucleotide_type,
    )
    if helper_error is None:
        return (
            f"Opened HDOCK nucleotide docking with receptor {receptor_file.name} "
            f"and {nucleotide_type} sequence ({len(sequence)} nt)."
        )
    webbrowser.open("http://hdock.phys.hust.edu.cn/")
    return (
        f"Opened HDOCK. Receptor PDB exported to {receptor_file}; copied {nucleotide_type} FASTA to clipboard. "
        f"Automatic upload/fill failed: {helper_error}"
    )


def launch_nucleotide_alphafold_pipeline(session, request_text=None):
    if not str(request_text or "").strip():
        request_text = _prompt_nucleotide_sequence(session)
    sequence, requested = _parse_nucleotide_request(request_text)
    if not sequence:
        return "No nucleotide sequence was provided."
    invalid = sorted(set(sequence) - set("ACGTU"))
    if invalid:
        return f"Nucleotide sequence contains unsupported letters: {''.join(invalid)}"
    protein_entries = _protein_chain_entries(session)
    if not protein_entries:
        return "No protein chain sequence was resolved for AlphaFold complex modeling."
    nucleotide_type = _infer_nucleotide_type(sequence, requested=requested)
    fasta = _fasta_text(protein_entries) + f">nucleotide_query|{nucleotide_type}\n{sequence}\n"
    _copy_text_to_clipboard(fasta)
    helper_error = _launch_with_browser_helper(["alphafold"], fasta)
    if helper_error is None:
        return (
            f"Opened AlphaFold Server with {len(protein_entries)} protein chain(s) "
            f"and one {nucleotide_type} sequence ({len(sequence)} nt)."
        )
    webbrowser.open("https://alphafoldserver.com")
    return (
        f"Opened AlphaFold Server and copied protein + {nucleotide_type} FASTA to clipboard. "
        f"Automatic fill failed: {helper_error}"
    )


def launch_boltz_panel(session):
    if _is_qt_main_thread():
        try:
            from chimerax.boltz.boltz_gui import boltz_panel

            panel = boltz_panel(session, create=True)
            try:
                panel.display(True)
            except Exception:
                pass
            return "Opened ChimeraX native Boltz panel."
        except Exception as err:
            raise err

    result_box = {}
    event = threading.Event()

    def runner():
        try:
            from chimerax.boltz.boltz_gui import boltz_panel

            panel = boltz_panel(session, create=True)
            try:
                panel.display(True)
            except Exception:
                pass
            result_box["message"] = "Opened ChimeraX native Boltz panel."
        except Exception as err:
            result_box["error"] = err
        finally:
            event.set()

    try:
        session.ui.thread_safe(runner)
    except Exception:
        runner()
    event.wait()
    if "error" in result_box:
        raise result_box["error"]
    return result_box.get("message", "Opened ChimeraX native Boltz panel.")


def _boltz_latest_executable():
    candidates = []
    env_path = os.environ.get("BOLTZ_EXE")
    if env_path:
        candidates.append(env_path)
    home = Path.home()
    candidates.extend(
        [
            str(home / "boltz2_latest" / "bin" / "boltz"),
            str(home / "boltz_latest" / "bin" / "boltz"),
            str(home / "boltz2" / "bin" / "boltz"),
        ]
    )
    found = shutil.which("boltz")
    if found:
        candidates.append(found)
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(Path(candidate).resolve())
    return None


def _boltz_sequence_entries(session):
    from chimerax.atomic import AtomicStructure, Residue

    entries = []
    used_ids = set()
    next_id = ord("A")
    for model in session.models.list():
        if not isinstance(model, AtomicStructure):
            continue
        for chain in getattr(model, "chains", []):
            polymer_type = getattr(chain, "polymer_type", None)
            if polymer_type == Residue.PT_AMINO:
                kind = "protein"
            elif polymer_type == Residue.PT_NUCLEIC:
                try:
                    sequence = str(chain.ungapped()).upper()
                except Exception:
                    sequence = ""
                kind = "rna" if "U" in sequence and "T" not in sequence else "dna"
            else:
                continue
            try:
                sequence = str(chain.ungapped()).strip().upper()
            except Exception:
                sequence = ""
            if not sequence:
                continue
            chain_id = str(getattr(chain, "chain_id", "") or "").strip()
            if not chain_id or chain_id in used_ids:
                while chr(next_id) in used_ids:
                    next_id += 1
                chain_id = chr(next_id)
            used_ids.add(chain_id)
            entries.append(
                {
                    "kind": kind,
                    "id": chain_id,
                    "sequence": sequence,
                    "model": model,
                    "spec": f"#{getattr(model, 'id_string', '?')}/{getattr(chain, 'chain_id', chain_id)}",
                }
            )
    return entries


def _write_boltz_latest_yaml(entries, yaml_path):
    lines = ["version: 1", "sequences:"]
    for entry in entries:
        kind = entry["kind"]
        lines.extend(
            [
                f"  - {kind}:",
                f"      id: {entry['id']}",
                f"      sequence: \"{entry['sequence']}\"",
            ]
        )
        if kind == "protein":
            lines.append("      msa: empty")
    yaml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _open_boltz_latest_outputs(session, output_dir):
    candidates = []
    for suffix in ("*.cif", "*.mmcif", "*.pdb"):
        candidates.extend(Path(output_dir).rglob(suffix))
    candidates = [
        path for path in candidates
        if path.is_file() and not path.name.startswith(".") and path.name != "input.yaml"
    ]
    if not candidates:
        return "Boltz completed, but no CIF/PDB output file was found under " + str(output_dir)
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    opened = candidates[:5]
    commands = []
    for path in opened:
        command = "open " + _quote_command_token(str(path))
        _run_chimerax(session, command)
        commands.append(command)
    try:
        _run_chimerax(session, "view")
    except Exception:
        pass
    return "\n".join(
        [
            f"Opened {len(opened)} Boltz output model(s).",
            "Output folder: " + str(output_dir),
            "Opened files:",
            *[f"- {path}" for path in opened],
        ]
    )


def _monitor_boltz_latest(session, process, output_dir, command_text):
    stdout, stderr = process.communicate()
    stdout_text = stdout.decode("utf-8", errors="replace") if isinstance(stdout, bytes) else str(stdout or "")
    stderr_text = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else str(stderr or "")

    def finish():
        if process.returncode != 0:
            detail = (stderr_text or stdout_text or "").strip()
            if len(detail) > 3000:
                detail = detail[-3000:]
            session.logger.error(
                "Boltz latest prediction failed.\n"
                f"Command: {command_text}\n"
                f"Exit code: {process.returncode}\n"
                f"{detail}"
            )
            return
        if stdout_text.strip():
            session.logger.info(stdout_text.strip()[-3000:])
        message = _open_boltz_latest_outputs(session, output_dir)
        session.logger.info(message)

    try:
        session.ui.thread_safe(finish)
    except Exception:
        finish()


def launch_boltz_latest_predict(session, *, executable=None, model="boltz2"):
    entries = _boltz_sequence_entries(session)
    if not entries:
        return "No protein/DNA/RNA chains are open for Boltz. Open a model or use the native Boltz panel."

    boltz_exe = executable or _boltz_latest_executable()
    if not boltz_exe:
        try:
            panel_message = launch_boltz_panel(session)
        except Exception:
            panel_message = "Could not open native Boltz panel."
        return "\n".join(
            [
                "Official latest Boltz CLI was not found.",
                "Install it in a separate environment, then click Boltz again:",
                "python3 -m venv ~/boltz2_latest",
                "~/boltz2_latest/bin/python -m pip install -U pip",
                "~/boltz2_latest/bin/python -m pip install -U boltz",
                "Expected executable: ~/boltz2_latest/bin/boltz",
                panel_message,
            ]
        )

    import time

    run_root = Path.home() / "Downloads" / "ChimeraX" / "BoltzLatest"
    run_root.mkdir(parents=True, exist_ok=True)
    run_dir = run_root / time.strftime("codex_boltz_%Y%m%d_%H%M%S")
    input_dir = run_dir / "input"
    output_dir = run_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = input_dir / "current_chimerax.yaml"
    _write_boltz_latest_yaml(entries, yaml_path)

    command = [
        boltz_exe,
        "predict",
        str(yaml_path),
        "--use_msa_server",
        "--use_potentials",
        "--out_dir",
        str(output_dir),
        "--model",
        str(model or "boltz2"),
        "--override",
    ]
    command_text = " ".join(_quote_command_token(part) for part in command)
    env = os.environ.copy()
    if sys.platform == "darwin":
        env.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    threading.Thread(
        target=_monitor_boltz_latest,
        args=(session, process, output_dir, command_text),
        daemon=True,
    ).start()
    specs = ", ".join(entry["spec"] for entry in entries)
    return "\n".join(
        [
            f"Started official latest Boltz prediction ({model or 'boltz2'}) for {len(entries)} chain(s): {specs}.",
            "Input YAML: " + str(yaml_path),
            "Output folder: " + str(output_dir),
            "Command: " + command_text,
        ]
    )


def _run_toolbar_action_direct(session, label, fn):
    _report_toolbar_result(session, f"{label}: starting...")
    try:
        message = fn()
    except Exception as err:
        _report_toolbar_result(session, f"{label} failed: {err}", error=True)
        return
    _report_toolbar_result(session, message or f"{label}: done")


def _similar_set_names(session):
    manager = getattr(session, "similar_structures", None)
    if manager is None:
        return []
    try:
        return list(manager.names)
    except Exception:
        return []


def _remember_latest_similar_set(session, before_names=None):
    current = _similar_set_names(session)
    before_names = list(before_names or [])
    new_names = [item for item in current if item not in before_names]
    if new_names:
        session._codex_bridge_last_similar_name = new_names[-1]
        return new_names[-1]
    if current:
        session._codex_bridge_last_similar_name = current[-1]
        return current[-1]
    return None


def _similar_results(session, set_name=None):
    try:
        from chimerax.similarstructures.simstruct import similar_structure_results

        return similar_structure_results(session, set_name)
    except Exception:
        return None


def _model_identity_set(session):
    try:
        return {id(model) for model in session.models.list()}
    except Exception:
        return set()


def _new_models_since(session, before_ids):
    try:
        return [model for model in session.models.list() if id(model) not in before_ids]
    except Exception:
        return []


def _foldseek_database_name(database):
    text = str(database or "").strip().lower()
    if text in {"afdb", "afdb50", "alphafold", "alphafold db", "alphafold database"}:
        return "afdb50"
    if text in {"afdb-swissprot", "swissprot", "uniprot"}:
        return "afdb-swissprot"
    if text in {"afdb-proteome", "proteome"}:
        return "afdb-proteome"
    return "pdb100"


def _entry_for_model(model):
    model_id = getattr(model, "id_string", "")
    if not model_id:
        return None
    return {
        "model": model,
        "spec": f"#{model_id}",
        "name": getattr(model, "name", "model") or "model",
    }


def _entries_for_models(models):
    entries = []
    for model in models:
        entry = _entry_for_model(model)
        if entry is not None:
            entries.append(entry)
    return entries


def _bytes_row_to_sequence(row):
    return "".join(chr(int(value)) if int(value) else "-" for value in row)


def _query_residue_for_alignment_column(results, column_index):
    qstart, _qend = results.query_alignment_range()
    query_index = qstart - 1 + column_index
    qres = results.query_residues
    if getattr(results, "_alignment_indexing", "sequence") == "coordinates":
        return qres[query_index] if 0 <= query_index < len(qres) else None
    sequence_to_coordinate = getattr(results, "_query_sequence_to_coord_index", None) or {}
    coordinate_index = sequence_to_coordinate.get(query_index)
    if coordinate_index is None:
        return None
    return qres[coordinate_index] if 0 <= coordinate_index < len(qres) else None


def _show_similar_multi_alignment(session, results, set_name, *, max_hits=40):
    hits = list(getattr(results, "hits", []) or [])[:max(1, int(max_hits or 40))]
    if not hits:
        return None

    alignment_array = results.sequence_alignment_array()
    rows = [alignment_array[0]]
    rows.extend(alignment_array[index + 1] for index in range(min(len(hits), alignment_array.shape[0] - 1)))

    query_chain = results.query_chain
    query_name = "query"
    if query_chain is not None:
        query_name = f"{query_chain.structure.name}_{query_chain.chain_id}"

    from chimerax.atomic import Sequence, SeqMatchMap

    seqs = [Sequence(name=query_name, characters=_bytes_row_to_sequence(rows[0]))]
    for hit, row in zip(hits, rows[1:]):
        hit_name = str(hit.get("database_full_id") or hit.get("database_id") or "hit")
        identity = hit.get("pident")
        evalue = hit.get("evalue")
        label = hit_name
        if identity is not None:
            try:
                label += f" | id {float(identity):.0f}%"
            except Exception:
                label += f" | id {identity}"
        if evalue is not None:
            label += f" | E {evalue}"
        seqs.append(Sequence(name=label, characters=_bytes_row_to_sequence(row)))

    alignment_name = f"Foldseek multi-hit alignment {set_name}"
    alignment = session.alignments.new_alignment(
        seqs,
        identify_as=f"{set_name}_multi",
        name=alignment_name,
        auto_associate=False,
        intrinsic=True,
    )
    session._codex_bridge_last_alignment_id = getattr(alignment, "ident", None)

    if query_chain is not None:
        try:
            match_map = SeqMatchMap(seqs[0], query_chain)
            errors = 0
            for pos in range(len(seqs[0])):
                residue = _query_residue_for_alignment_column(results, pos)
                if residue is not None:
                    match_map.match(residue, pos)
            alignment.prematched_assoc_structure(match_map, errors, None)
        except Exception:
            pass

    _resize_alignment_viewer(session, alignment)
    return alignment


def _resize_alignment_viewer(session, alignment, *, width=1080, height=360):
    def apply_size():
        try:
            from chimerax.seq_view.tool import SequenceViewer
        except Exception:
            return
        for tool in session.tools.list():
            if not isinstance(tool, SequenceViewer):
                continue
            if getattr(tool, "alignment", None) is not alignment:
                continue
            window = getattr(tool, "tool_window", None)
            area = getattr(window, "ui_area", None)
            dock = getattr(window, "_dock_widget", None)
            for widget in (area, dock):
                if widget is None:
                    continue
                try:
                    widget.setMinimumSize(min(width, 900), min(height, 320))
                except Exception:
                    pass
                try:
                    widget.resize(width, height)
                except Exception:
                    pass
            try:
                window.shown = True
            except Exception:
                pass
            break

    try:
        from Qt.QtCore import QTimer

        QTimer.singleShot(0, apply_size)
        QTimer.singleShot(300, apply_size)
    except Exception:
        apply_size()


def _open_similar_hits_from_results(session, results, set_name, *, count=3, trim=True, align=True):
    try:
        hit_count = max(int(count), 1)
    except Exception:
        hit_count = 3
    hits = list(getattr(results, "hits", []) or [])[:hit_count]
    opened_models = []
    opened_names = []

    for hit in hits:
        hit_name = str(hit.get("database_full_id") or hit.get("database_id") or "").strip()
        if not hit_name:
            continue
        before_models = _model_identity_set(session)
        results.open_hit(
            session,
            hit,
            trim=trim,
            align=align,
            alignment_cutoff_distance=getattr(results, "alignment_cutoff_distance", None),
            in_file_history=(len(hits) == 1),
            log=True,
        )
        opened_names.append(hit_name)
        opened_models.extend(_new_models_since(session, before_models))

    if opened_models:
        try:
            _show_similar_multi_alignment(session, results, set_name, max_hits=40)
        except Exception:
            pass
        try:
            specs = " ".join(f"#{getattr(model, 'id_string', '?')}" for model in opened_models)
            _run_chimerax(session, "view " + specs)
        except Exception:
            pass

    message = "\n".join(
        [
            f"Foldseek result set {set_name} opened {len(opened_models) or len(opened_names)} aligned hit structure(s).",
            "Opened hits: " + (", ".join(opened_names[:hit_count]) if opened_names else "(none)"),
        ]
    )
    return opened_models, opened_names, message


def _install_foldseek_result_handler(session, before_names, *, count=3, label="Foldseek", post_open=None, timeout_seconds=900):
    import time

    start_time = time.time()
    before_names = list(before_names or [])

    def check_results(*_args):
        current = _similar_set_names(session)
        new_names = [name for name in current if name not in before_names]
        if not new_names:
            if time.time() - start_time > timeout_seconds:
                session.logger.warning(f"{label}: timed out waiting for Foldseek results.")
                return "delete handler"
            return None

        set_name = new_names[-1]
        results = _similar_results(session, set_name)
        if results is None or not getattr(results, "hits", None):
            return None

        try:
            opened_models, opened_names, message = _open_similar_hits_from_results(
                session,
                results,
                set_name,
                count=count,
            )
            session.logger.info(message)
            if post_open is not None:
                post_open(results, opened_models, opened_names, message)
        except Exception as err:
            session.logger.error(f"{label}: failed to open Foldseek hits: {err}")
        return "delete handler"

    def add_handler():
        session.triggers.add_handler("new frame", check_results)

    try:
        session.ui.thread_safe(add_handler)
    except Exception:
        add_handler()


def launch_foldseek_open_aligned(session, *, count=3, database="pdb100", executor=None):
    try:
        hit_count = max(int(count), 1)
    except Exception:
        hit_count = 3
    chains = _protein_chain_specs(session)
    if not chains:
        return "No protein chain was resolved for Foldseek. Select a protein chain or open a protein model first."

    chain_spec = chains[0]
    foldseek_database = _foldseek_database_name(database)
    before_sets = _similar_set_names(session)
    search_command = f"foldseek {chain_spec} database {foldseek_database} showTable true"
    _run_chimerax(session, search_command, executor=executor)
    _install_foldseek_result_handler(
        session,
        before_sets,
        count=hit_count,
        label="Similar",
    )
    return "\n".join(
        [
            f"Submitted Foldseek search for {chain_spec} in {foldseek_database}.",
            f"When the built-in Similar Structures result set appears, Codex will open and align the top {hit_count} hit(s).",
            f"Executed ChimeraX command: {search_command}",
        ]
    )


def launch_similar_open_aligned(session, *, count=3, database="pdb100", executor=None):
    return launch_foldseek_open_aligned(session, count=count, database=database, executor=executor)


def _launch_structure_upload_site(session, *, label, site_key, url, fmt="pdb", executor=None):
    structure_file, out_dir = _export_first_structure_file(
        session,
        fmt=fmt,
        prefix=f"chimerax_{site_key}",
        executor=executor,
    )
    if structure_file is None:
        return f"No atomic structure is open for {label}."
    _copy_text_to_clipboard(str(structure_file))
    helper_error = _launch_with_browser_helper([site_key], structureFiles=[str(structure_file)])
    if helper_error is None:
        return f"Opened {label} and uploaded {structure_file.name} in Chrome."
    webbrowser.open(url)
    return (
        f"Opened {label}. Exported query structure to {structure_file}; file path was copied to clipboard. "
        f"Automatic upload failed: {helper_error}"
    )


def launch_dali_server(session, *, executor=None):
    return _launch_structure_upload_site(
        session,
        label="DALI",
        site_key="dali",
        url=DALI_URL,
        fmt="pdb",
        executor=executor,
    )


def launch_vast_search(session, *, executor=None):
    return _launch_structure_upload_site(
        session,
        label="NCBI VAST",
        site_key="vast",
        url=VAST_URL,
        fmt="pdb",
        executor=executor,
    )


def launch_pdbefold_search(session, *, executor=None):
    return _launch_structure_upload_site(
        session,
        label="PDBeFold / SSM",
        site_key="pdbefold",
        url=PDBEFOLD_URL,
        fmt="pdb",
        executor=executor,
    )


def launch_pisa_server(session, *, executor=None):
    return _launch_structure_upload_site(
        session,
        label="PDBePISA",
        site_key="pisa",
        url=PISA_URL,
        fmt="pdb",
        executor=executor,
    )


def launch_caver_server(session, *, executor=None):
    return _launch_structure_upload_site(
        session,
        label="CAVER Web",
        site_key="caver",
        url=CAVER_URL,
        fmt="pdb",
        executor=executor,
    )


def launch_usalign(session, *, executor=None):
    files, out_dir = _export_model_files(
        session,
        fmt="pdb",
        selected_preferred=True,
        prefix="chimerax_usalign",
        executor=executor,
    )
    if len(files) < 2:
        return "US-align needs at least two open/selected atomic structures. Open or select two structures first."
    upload_files = files[:10]
    _copy_text_to_clipboard("\n".join(str(path) for path in upload_files))
    helper_error = _launch_with_browser_helper(["usalign"], structureFiles=[str(path) for path in upload_files])
    if helper_error is None:
        return f"Opened US-align and uploaded {len(upload_files)} structure file(s) in Chrome."
    webbrowser.open(USALIGN_URL)
    return (
        f"Opened US-align. Exported {len(upload_files)} structures under {out_dir}; paths were copied to clipboard. "
        f"Automatic upload failed: {helper_error}"
    )


def launch_foldseek_foldmason(session, *, count=5, database="pdb100", executor=None):
    try:
        hit_count = max(int(count), 1)
    except Exception:
        hit_count = 5
    entries = _atomic_model_entries(session, selected_preferred=True)
    if not entries:
        entries = _atomic_model_entries(session, selected_preferred=False)
    query_entry = entries[0] if entries else None

    chains = _protein_chain_specs(session)
    if not chains:
        return "No protein chain was resolved for FoldMason auto-search. Open a protein model first."

    chain_spec = chains[0]
    foldseek_database = _foldseek_database_name(database)
    before_sets = _similar_set_names(session)
    search_command = f"foldseek {chain_spec} database {foldseek_database} showTable true"

    def finish_foldmason(_results, opened_models, _opened_names, open_message):
        if not opened_models:
            session.logger.warning("FoldMason auto-search found no structures to export.")
            return
        entries_to_export = []
        if query_entry is not None:
            entries_to_export.append(query_entry)
        entries_to_export.extend(_entries_for_models(opened_models))
        files, out_dir = _export_model_entries(
            session,
            entries_to_export,
            fmt="mmcif",
            prefix="chimerax_foldmason",
        )

        def worker():
            message = _launch_foldmason_with_files(session, files, out_dir, auto_report=open_message)
            _report_toolbar_result(session, message)

        threading.Thread(target=worker, daemon=True).start()

    _run_chimerax(session, search_command, executor=executor)
    _install_foldseek_result_handler(
        session,
        before_sets,
        count=hit_count,
        label="FoldMason",
        post_open=finish_foldmason,
    )
    return "\n".join(
        [
            f"Submitted Foldseek search for {chain_spec} in {foldseek_database}.",
            f"When results arrive, Codex will open/align top {hit_count} hit(s), then launch FoldMason with the query plus those hits.",
            f"Executed ChimeraX command: {search_command}",
        ]
    )


def _launch_hmmer_page(session):
    entries = _protein_chain_entries(session)
    if not entries:
        return "No protein chain sequence was resolved for HMMER."
    fasta = _fasta_text([entries[0]])
    _copy_text_to_clipboard(fasta)
    helper_error = _launch_with_browser_helper(["hmmer"], fasta)
    if helper_error is None:
        return f"Opened HMMER phmmer for {entries[0]['spec']} and filled the sequence in Chrome."
    webbrowser.open("https://www.ebi.ac.uk/Tools/hmmer/search/phmmer")
    return f"Opened HMMER phmmer for {entries[0]['spec']} and copied FASTA to the clipboard, but automatic page filling failed: {helper_error}"


def _launch_interpro_page(session):
    entries = _protein_chain_entries(session)
    if not entries:
        return "No protein chain sequence was resolved for InterPro/Pfam."
    fasta = _fasta_text([entries[0]])
    _copy_text_to_clipboard(fasta)
    helper_error = _launch_with_browser_helper(["interpro"], fasta)
    if helper_error is None:
        return f"Opened InterPro/Pfam for {entries[0]['spec']} and filled the sequence in Chrome."
    webbrowser.open("https://www.ebi.ac.uk/interpro/search/sequence/")
    return f"Opened InterPro/Pfam for {entries[0]['spec']} and copied FASTA to the clipboard, but automatic page filling failed: {helper_error}"


def _launch_ncbi_page(session):
    entries = _protein_chain_entries(session)
    if not entries:
        return "No protein chain sequence was resolved for NCBI BLAST."
    fasta = _fasta_text([entries[0]])
    _copy_text_to_clipboard(fasta)
    helper_error = _launch_with_browser_helper(["ncbi"], fasta)
    if helper_error is None:
        return f"Opened NCBI BLASTP for {entries[0]['spec']} and filled the sequence in Chrome."
    webbrowser.open(
        "https://blast.ncbi.nlm.nih.gov/Blast.cgi?PAGE_TYPE=BlastSearch&PROGRAM=blastp&QUERY="
        + quote(fasta, safe="")
    )
    return f"Opened NCBI BLASTP for {entries[0]['spec']} and copied FASTA to the clipboard, but automatic page filling failed: {helper_error}"


def _launch_hhpred_page(session):
    entries = _protein_chain_entries(session)
    if not entries:
        return "No protein chain sequence was resolved for HHpred."
    fasta = _fasta_text([entries[0]])
    _copy_text_to_clipboard(fasta)
    helper_error = _launch_with_browser_helper(["hhpred"], fasta)
    if helper_error is None:
        return f"Opened HHpred / HHblits for {entries[0]['spec']} and filled the sequence in Chrome."
    ok, detail = _run_safari_prefill(HHPRED_URL, _generic_sequence_fill_js(fasta), delays=(4, 8, 14))
    if ok:
        return f"Opened HHpred / HHblits for {entries[0]['spec']} and copied FASTA to the clipboard."
    webbrowser.open(HHPRED_URL)
    return (
        f"Opened HHpred / HHblits for {entries[0]['spec']} and copied FASTA to the clipboard, "
        f"but automatic page filling failed: {helper_error}; Safari fallback: {detail}"
    )


def _launch_consurf_page(session):
    entries = _protein_chain_entries(session)
    if not entries:
        return "No protein chain sequence was resolved for ConSurf."
    fasta = _fasta_text(entries[:1])
    _copy_text_to_clipboard(fasta)
    helper_error = _launch_with_browser_helper(["consurf"], fasta)
    if helper_error is None:
        return f"Opened ConSurf Colab for {entries[0]['spec']} and filled/copied the first protein-chain FASTA in Chrome."

    js_code = _generic_sequence_fill_js(fasta)
    ok, detail = _run_safari_prefill(CONSURF_COLAB_URL, js_code, delays=(4, 8, 14))
    if ok:
        return (
            f"Opened ConSurf Colab for {entries[0]['spec']} and copied the first protein-chain FASTA. "
            "Safari automatic fill was attempted."
        )
    webbrowser.open(CONSURF_COLAB_URL)
    return (
        f"Opened ConSurf Colab for {entries[0]['spec']} and copied the first protein-chain FASTA to the clipboard, "
        f"but automatic page filling failed: {helper_error}; Safari fallback: {detail}"
    )


def _launch_alphafold_server(session):
    entries = _protein_chain_entries(session)
    if not entries:
        return "No protein chain sequence was resolved for AlphaFold Server."
    fasta = _fasta_text(entries)
    _copy_text_to_clipboard(fasta)
    helper_error = _launch_with_browser_helper(["alphafold"], fasta)
    if helper_error is None:
        return f"Opened AlphaFold Server and filled {len(entries)} protein-chain sequence(s) in Chrome."
    js_code = f"""
(() => {{
  const value = {json.dumps(fasta)};
  const apply = () => {{
    const target = Array.from(document.querySelectorAll('textarea,input[type="text"],input:not([type])')).find(
      el => {{
        const placeholder = (el.placeholder || '').toLowerCase();
        return !placeholder || placeholder.includes('sequence') || placeholder.includes('protein');
      }}
    );
    if (!target) return false;
    const proto = target.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
    if (!descriptor || !descriptor.set) return false;
    descriptor.set.call(target, value);
    target.dispatchEvent(new Event('input', {{ bubbles: true }}));
    target.dispatchEvent(new Event('change', {{ bubbles: true }}));
    target.focus();
    return true;
  }};
  if (apply()) return 'filled';
  let attempts = 0;
  const timer = setInterval(() => {{
    attempts += 1;
    if (apply() || attempts > 60) clearInterval(timer);
  }}, 500);
  return 'waiting';
}})();
""".strip()
    ok, detail = _run_safari_prefill("https://alphafoldserver.com", js_code)
    if ok:
        return f"Opened AlphaFold Server and copied {len(entries)} protein-chain sequence(s) in FASTA format."
    return (
        f"Opened AlphaFold Server and copied {len(entries)} protein-chain sequence(s) in FASTA format, "
        f"but automatic page filling failed: {detail}"
    )


def run_toolbar_action(session, name):
    action = str(name or "").strip().lower()

    if action in {"ai-quick-analyze", "ai-quick-view", "ai-quick-site", "ai-quick-figure"}:
        if action == "ai-quick-analyze":
            _show_ai_prompt(
                session,
                "Analyze the current ChimeraX view with evidence, confidence, and next checks.",
            )
        elif action == "ai-quick-view":
            _show_ai_prompt(
                session,
                "Improve the current ChimeraX view for interpretability and apply a safe view change.",
            )
        elif action == "ai-quick-site":
            _show_ai_prompt(
                session,
                "Identify the most likely active site or functional pocket in the current view.",
            )
        elif action == "ai-quick-figure":
            _show_ai_prompt(
                session,
                "Build the clearest figure-ready structural view from the current scene.",
            )
        return

    if action == "ai-analysis-blast":
        chosen_sites = _choose_sequence_analysis_sites(session)
        if not chosen_sites:
            _report_toolbar_result(session, "Blast: canceled")
            return
        _run_toolbar_task(session, "Blast", lambda: _launch_sequence_analysis_tabs(session, chosen_sites))
        return
    elif action == "ai-analysis-alphafold":
        _run_toolbar_task(session, "AlphaFold", lambda: _launch_alphafold_server(session))
        return
    elif action == "ai-analysis-similar":
        _run_toolbar_chimerax_task(
            session,
            "Similar",
            lambda executor: launch_similar_open_aligned(session, executor=executor),
        )
        return
    elif action == "ai-analysis-profile":
        _run_toolbar_task(session, "Profile", lambda: _launch_uniprot_blast_page(session))
        return
    elif action == "ai-analysis-hhpred":
        _run_toolbar_task(session, "HHpred", lambda: _launch_hhpred_page(session))
        return
    elif action == "ai-analysis-catalytic":
        def catalytic_task(executor):
            from .builtin_actions import _run_catalytic_view

            return _run_catalytic_view(session, executor=executor)

        _run_toolbar_chimerax_task(session, "Catalytic", catalytic_task)
        return
    elif action == "ai-analysis-membrane":
        def membrane_task(executor):
            from .membrane import run_membrane_view

            return run_membrane_view(session, executor=executor)

        _run_toolbar_chimerax_task(session, "Membrane", membrane_task)
        return
    elif action == "ai-analysis-pisa":
        def pisa_task(executor):
            from .pisa import run_pisa_view

            return run_pisa_view(session, "view", executor=executor)

        _run_toolbar_chimerax_task(session, "PISA", pisa_task)
        return
    elif action == "ai-analysis-caver":
        _run_toolbar_chimerax_task(session, "CAVER", lambda executor: launch_caver_server(session, executor=executor))
        return
    elif action == "ai-analysis-dali":
        _run_toolbar_chimerax_task(session, "DALI", lambda executor: launch_dali_server(session, executor=executor))
        return
    elif action == "ai-analysis-vast":
        _run_toolbar_chimerax_task(session, "VAST", lambda executor: launch_vast_search(session, executor=executor))
        return
    elif action == "ai-analysis-pdbefold":
        _run_toolbar_chimerax_task(
            session,
            "PDBeFold",
            lambda executor: launch_pdbefold_search(session, executor=executor),
        )
        return
    elif action == "ai-analysis-usalign":
        _run_toolbar_chimerax_task(session, "US-align", lambda executor: launch_usalign(session, executor=executor))
        return
    elif action == "ai-analysis-conserve":
        _run_toolbar_task(session, "Consurf", lambda: _launch_consurf_page(session))
        return
    elif action in {"ai-analysis-boltz", "ai-nucleotide-boltz"}:
        _run_toolbar_task(session, "Boltz", lambda: launch_boltz_latest_predict(session))
        return
    elif action in {"ai-analysis-foldmason", "ai-nucleotide-foldmason"}:
        _run_toolbar_chimerax_task(
            session,
            "FoldMason",
            lambda executor: launch_foldmason(session, executor=executor),
        )
        return
    elif action in {"ai-analysis-folddisco", "ai-nucleotide-folddisco"}:
        _run_toolbar_chimerax_task(
            session,
            "FoldDisco",
            lambda executor: launch_folddisco(session, executor=executor),
        )
        return
    elif action == "ai-analysis-nucdock":
        request_text = _prompt_nucleotide_sequence(session)
        if request_text is None:
            _report_toolbar_result(session, "NucDock: canceled")
            return
        _run_toolbar_chimerax_task(
            session,
            "NucDock",
            lambda executor: launch_nucleotide_docking_pipeline(session, request_text, executor=executor),
        )
        return
    elif action == "ai-analysis-afcomplex":
        request_text = _prompt_nucleotide_sequence(session)
        if request_text is None:
            _report_toolbar_result(session, "AF Complex: canceled")
            return
        _run_toolbar_task(session, "AF Complex", lambda: launch_nucleotide_alphafold_pipeline(session, request_text))
        return
    elif action == "ai-display-controls":
        from .display_controls import CodexDisplayControls

        _toggle_singleton_tool(session, CodexDisplayControls, "Display Controls")
        return
    elif action == "ai-nucleotide-dock":
        request_text = _prompt_nucleotide_sequence(session)
        if request_text is None:
            _report_toolbar_result(session, "NucDock: canceled")
            return
        _run_toolbar_chimerax_task(
            session,
            "NucDock",
            lambda executor: launch_nucleotide_docking_pipeline(session, request_text, executor=executor),
        )
        return
    elif action == "ai-nucleotide-afcomplex":
        request_text = _prompt_nucleotide_sequence(session)
        if request_text is None:
            _report_toolbar_result(session, "AF Complex: canceled")
            return
        _run_toolbar_task(session, "AF Complex", lambda: launch_nucleotide_alphafold_pipeline(session, request_text))
        return
