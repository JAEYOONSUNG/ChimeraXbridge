import json
import os
import csv
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import tempfile
import time
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
RAPIDOCK_GITHUB_URL = "https://github.com/huifengzhao/RAPiDock"
RAPIDOCK_DEFAULT_REPO = os.environ.get("RAPIDOCK_REPO", "")

# PyRosetta scoring helper that runs INSIDE the rapidock Docker image.
# argv: [in_pdb, out_json]
_PYROSETTA_SCORE_SCRIPT = r'''
import sys, json
in_pdb, out_json = sys.argv[1], sys.argv[2]
result = {}
try:
    import pyrosetta
    pyrosetta.init("-mute all")
    from pyrosetta.rosetta.core.scoring import ScoreFunctionFactory
    sfxn = ScoreFunctionFactory.create_score_function("ref2015")
    pose = pyrosetta.pose_from_pdb(in_pdb)
    total = float(sfxn(pose))
    result["total_score"] = round(total, 3)
    n = pose.total_residue()
    result["per_residue_avg"] = round(total / max(n, 1), 3)
    result["n_residues"] = int(n)

    # Multi-chain interface analysis
    chains = sorted({pose.pdb_info().chain(i + 1) for i in range(n)})
    if len(chains) >= 2:
        # Treat the smallest chain as the "peptide", others as "receptor".
        sizes = {c: 0 for c in chains}
        for i in range(n):
            sizes[pose.pdb_info().chain(i + 1)] += 1
        peptide_chain = min(sizes, key=sizes.get)
        receptor_chains = [c for c in chains if c != peptide_chain]
        # Build chain-jump string for InterfaceAnalyzer
        from pyrosetta.rosetta.protocols.analysis import InterfaceAnalyzerMover
        ia = InterfaceAnalyzerMover()
        # Format: "RECEPTORCHAINS_PEPTIDECHAIN"  e.g. "A_B"
        interface_str = "".join(receptor_chains) + "_" + peptide_chain
        try:
            ia.set_interface(interface_str)
        except Exception:
            pass
        try:
            ia.set_pack_separated(True)
            ia.set_compute_packstat(True)
            ia.set_scorefunction(sfxn)
            ia.apply(pose)
            iface = {
                "peptide_chain": peptide_chain,
                "receptor_chains": "".join(receptor_chains),
                "dG_separated": round(float(ia.get_separated_interface_energy()), 3),
                "dSASA": round(float(ia.get_interface_delta_sasa()), 1),
                "hbonds": int(ia.get_num_interface_hbonds()),
                "packstat": round(float(ia.get_interface_packstat()), 3),
            }
            result["interface"] = iface
        except Exception as exc:
            result["interface_error"] = str(exc)
except Exception as exc:
    result["error"] = str(exc)

with open(out_json, "w") as fh:
    json.dump(result, fh, indent=2)
print(json.dumps(result, indent=2))
'''



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
    if not event.wait(120):
        raise TimeoutError(
            f"toolbar UI bounce did not complete within 120s: {command!r}"
        )
    if "error" in result_box:
        raise result_box["error"]
    return result_box.get("result")


def _call_ui_thread(session, fn, *, timeout=300):
    if _is_qt_main_thread():
        return fn()
    result_box = {}
    event = threading.Event()

    def runner():
        try:
            result_box["result"] = fn()
        except Exception as err:
            result_box["error"] = err
        finally:
            event.set()

    try:
        session.ui.thread_safe(runner)
    except Exception:
        runner()
    if not event.wait(timeout):
        raise TimeoutError("UI prompt did not complete in time")
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


def _prompt_rmsd_options(session):
    """Modal dialog: reference + multi-target picker + mode + cutoff."""
    try:
        from Qt.QtCore import Qt
        from Qt.QtWidgets import (
            QAbstractItemView,
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QDoubleSpinBox,
            QFormLayout,
            QLabel,
            QListWidget,
            QListWidgetItem,
            QRadioButton,
            QVBoxLayout,
        )
        from chimerax.atomic import AtomicStructure
    except Exception:
        return None

    candidates = []
    try:
        for model in session.models.list(type=AtomicStructure):
            spec = f"#{model.id_string}"
            name = (getattr(model, "name", "") or "").strip()
            candidates.append((f"{spec}  {name}".strip(), spec))
    except Exception:
        pass
    if len(candidates) < 2:
        try:
            session.logger.warning(
                "RMSD: need at least 2 open atomic structures (1 reference + 1 target)."
            )
        except Exception:
            pass
        return None

    parent = getattr(getattr(session, "ui", None), "main_window", None)
    dialog = QDialog(parent)
    dialog.setWindowTitle("RMSD against reference")
    layout = QVBoxLayout(dialog)
    form = QFormLayout()

    ref_combo = QComboBox(dialog)
    for label, spec in candidates:
        ref_combo.addItem(label, spec)
    form.addRow("Reference", ref_combo)

    target_list = QListWidget(dialog)
    target_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    target_list.setMinimumHeight(140)
    for label, spec in candidates:
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, spec)
        target_list.addItem(item)
    form.addRow("Targets", target_list)

    cutoff_spin = QDoubleSpinBox(dialog)
    cutoff_spin.setRange(0.5, 10.0)
    cutoff_spin.setSingleStep(0.1)
    cutoff_spin.setDecimals(1)
    cutoff_spin.setSuffix(" Å")
    cutoff_spin.setValue(2.0)
    form.addRow("Pruning cutoff", cutoff_spin)

    realign_radio = QRadioButton("Re-align targets onto reference", dialog)
    realign_radio.setChecked(True)
    keep_radio = QRadioButton("Use current coordinates (do not move)", dialog)
    form.addRow("Mode", realign_radio)
    form.addRow("", keep_radio)

    note = QLabel(
        "Pruned RMSD: matched atoms within the cutoff after the optimal\n"
        "rigid-body fit. Native RMSD: every sequence-aligned atom pair,\n"
        "no pruning. \"Use current coordinates\" keeps each target where\n"
        "it currently sits and reports RMSDs on that pose.",
        dialog,
    )
    note.setWordWrap(True)
    layout.addLayout(form)
    layout.addWidget(note)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        Qt.Orientation.Horizontal,
        dialog,
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    ref_spec = ref_combo.currentData()
    target_specs = []
    for item in target_list.selectedItems():
        spec = item.data(Qt.ItemDataRole.UserRole)
        if spec and spec != ref_spec and spec not in target_specs:
            target_specs.append(spec)
    if not target_specs:
        try:
            session.logger.warning("RMSD: no target structures selected.")
        except Exception:
            pass
        return None
    return {
        "ref_spec": str(ref_spec),
        "target_specs": target_specs,
        "cutoff": float(cutoff_spin.value()),
        "realign": bool(realign_radio.isChecked()),
    }


def _tabify_helper_into_models_strip(session, *, raise_tool=None):
    """Bring the just-opened helper tool into the shared Models dock tab strip.

    Tries once immediately, then again after short delays because docks
    are wired into the main window asynchronously and the first call
    often runs before the new dock widget exists.
    """
    try:
        from . import _tabify_helper_tools
    except Exception:
        return
    try:
        _tabify_helper_tools(session, raise_tool=raise_tool)
    except Exception:
        pass
    try:
        from Qt.QtCore import QTimer

        for delay in (120, 400, 1200):
            QTimer.singleShot(
                delay,
                lambda ses=session, name=raise_tool: _tabify_helper_tools(ses, raise_tool=name),
            )
    except Exception:
        pass


def _result_failed_to_resolve(result_text):
    text = str(result_text or "").lower()
    return text.startswith("no ") or "no alignment is open" in text


def _protein_chain_specs(session, model_hint=None):
    from .semantic import get_session_semantics, resolve_default_model_spec, resolve_model_spec

    semantics = get_session_semantics(session)
    selection = semantics.get("selection", {})
    chains = []

    resolved_hint = resolve_model_spec(session, model_hint) if model_hint else None
    requested_chain = None
    hint_text = str(model_hint or "").strip()
    if "/" in hint_text:
        model_part, chain_part = hint_text.split("/", 1)
        model_part = model_part.strip()
        chain_part = chain_part.split(":", 1)[0].strip()
        resolved_model_part = resolve_model_spec(session, model_part) or model_part
        if resolved_model_part:
            resolved_hint = resolved_model_part
            requested_chain = chain_part or None

    if not resolved_hint:
        for token in selection.get("ranges", []):
            head = str(token).split(":", 1)[0]
            if "/" in head and head not in chains:
                chains.append(head)

        if chains:
            return chains

    target_models = [resolved_hint] if resolved_hint else list(selection.get("models", []))
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
            if requested_chain and str(chain.get("id", "")).strip() != requested_chain:
                continue
            spec = f"{model['spec']}/{chain['id']}"
            if spec not in chains:
                chains.append(spec)
    return chains


def _default_model_spec(session):
    from .semantic import resolve_default_model_spec

    return resolve_default_model_spec(session)


def _protein_chain_entries(session, model_hint=None):
    from chimerax.atomic import ChainArg

    entries = []
    for spec in _protein_chain_specs(session, model_hint=model_hint):
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
        try:
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False, timeout=5)
        except subprocess.TimeoutExpired:
            pass


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
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return False, "Safari prefill timed out after 60s"
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
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return False, "Safari tab-batch prefill timed out after 120s"
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


def _launch_uniprot_blast_page(session, model_hint=None):
    entries = _protein_chain_entries(session, model_hint=model_hint)
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
        "signalp": False,
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
        ("signalp", "SignalP 6.0"),
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


def _launch_sequence_analysis_tabs(session, chosen_sites=None, entries=None, model_hint=None):
    entries = list(entries) if entries is not None else _protein_chain_entries(session, model_hint=model_hint)
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
        "signalp": {
            "label": "SignalP 6.0",
            "url": "https://services.healthtech.dtu.dk/services/SignalP-6.0/",
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


def launch_sequence_analysis_site(session, site, entries=None, model_hint=None):
    site = str(site or "").strip().lower()
    if site == "uniprot":
        return _launch_uniprot_blast_page(session, model_hint=model_hint)
    if site == "ncbi":
        return _launch_ncbi_page(session, model_hint=model_hint)
    if site == "hmmer":
        return _launch_hmmer_page(session, model_hint=model_hint)
    if site == "interpro":
        return _launch_interpro_page(session, model_hint=model_hint)
    if site == "hhpred":
        return _launch_hhpred_page(session, model_hint=model_hint)
    if site == "signalp":
        from .signalp import launch_signalp_web

        return launch_signalp_web(session, model_hint=model_hint)
    if site == "rcsb":
        return _launch_sequence_analysis_tabs(session, ["rcsb"], entries=entries, model_hint=model_hint)
    if site == "alphafold":
        return _launch_alphafold_server(session, model_hint=model_hint)
    if site == "consurf":
        return _launch_consurf_page(session, model_hint=model_hint)
    return f"Unknown sequence-analysis site: {site}"


def _safe_file_stem(text, fallback="model"):
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or "").strip()).strip("._")
    return stem or fallback


def _atomic_child_structures(model, AtomicStructure=None):
    if model is None:
        return []
    if AtomicStructure is None:
        try:
            from chimerax.atomic import AtomicStructure
        except Exception:
            AtomicStructure = None
    children = []
    try:
        all_models = list(model.all_models())
    except Exception:
        all_models = [model]
    if model not in all_models:
        all_models.insert(0, model)
    seen = set()
    for item in all_models:
        ident = id(item)
        if ident in seen:
            continue
        seen.add(ident)
        if AtomicStructure is not None and not isinstance(item, AtomicStructure):
            continue
        if not hasattr(item, "atoms") or not hasattr(item, "residues"):
            continue
        try:
            atoms = getattr(item, "atoms", None)
            if atoms is not None and len(atoms) == 0:
                continue
        except Exception:
            pass
        children.append(item)
    return children


def _iter_atomic_structures(session):
    from chimerax.atomic import AtomicStructure

    seen = set()
    for model in session.models.list():
        for atomic_model in _atomic_child_structures(model, AtomicStructure):
            ident = id(atomic_model)
            if ident in seen:
                continue
            seen.add(ident)
            yield atomic_model


def _atomic_model_display_name(model):
    name = str(getattr(model, "name", "") or "model").strip() or "model"
    parent = getattr(model, "parent", None)
    try:
        root = model.session.models.scene_root_model
    except Exception:
        root = None
    parent_name = str(getattr(parent, "name", "") or "").strip()
    if parent is not None and parent is not root and parent_name and parent_name != name:
        return f"{parent_name} / {name}"
    return name


def _atomic_model_entry(model):
    model_id = str(getattr(model, "id_string", "") or "")
    if not model_id:
        return None
    return {
        "model": model,
        "spec": f"#{model_id}",
        "name": _atomic_model_display_name(model),
    }


def _atomic_model_entries(session, *, selected_preferred=False):
    selected_ids = set()
    if selected_preferred:
        selected_ids = {
            spec.lstrip("#")
            for spec in _selected_atomic_model_specs(session)
            if str(spec or "").startswith("#")
        }
        if not selected_ids:
            try:
                from chimerax.atomic import selected_residues

                for structure, _chain_id, _residues in selected_residues(session).by_chain:
                    selected_ids.add(getattr(structure, "id_string", ""))
            except Exception:
                pass

    entries = []
    for model in _iter_atomic_structures(session):
        model_id = getattr(model, "id_string", "")
        if selected_ids and model_id not in selected_ids:
            continue
        entry = _atomic_model_entry(model)
        if entry is not None:
            entries.append(entry)
    if selected_ids and entries:
        return entries
    if selected_preferred:
        return _atomic_model_entries(session, selected_preferred=False)
    return entries


def _model_panel_atomic_model_entries(session):
    entries = []
    seen = set()
    try:
        from chimerax.atomic import AtomicStructure
        from chimerax.model_panel.tool import ModelPanel

        panel = ModelPanel.get_singleton(session)
        if panel is None:
            return entries
        panel_models = list(getattr(panel, "models", []) or [])
        if not panel_models:
            return entries
        for model in panel_models:
            for atomic_model in _atomic_child_structures(model, AtomicStructure):
                ident = id(atomic_model)
                if ident in seen:
                    continue
                seen.add(ident)
                entry = _atomic_model_entry(atomic_model)
                if entry is not None:
                    entries.append(entry)
    except Exception:
        pass
    return entries


def _alignment_atomic_model_entries(session):
    entries = []
    seen = set()
    for source_entries in (
        _model_panel_atomic_model_entries(session),
        _atomic_model_entries(session, selected_preferred=False),
    ):
        for entry in source_entries:
            model = entry.get("model")
            ident = id(model)
            if ident in seen:
                continue
            seen.add(ident)
            entries.append(entry)
    return entries


def _is_codex_helper_atomic_model(model):
    name = str(getattr(model, "name", "") or "").strip().lower()
    if name.startswith((
        "domain ",
        "group_",
        "cavity",
        "binding_pocket",
        "metal candidate",
        "predicted metal",
        "rapidock_",
        "hpepdock_",
        "afcomplex_",
    )):
        return True
    id_string = str(getattr(model, "id_string", "") or "")
    # KVFinder cavity point clouds commonly live as dotted submodels such as
    # #1.2.16.  They are visual overlays, not primary receptors for analysis.
    if "." in id_string and any(token in name for token in ("cavity", "pocket")):
        return True
    return False


def _default_toolbar_atomic_model_spec(session):
    selected = [
        entry for entry in _atomic_model_entries(session, selected_preferred=True)
        if not _is_codex_helper_atomic_model(entry["model"])
    ]
    if selected:
        return selected[0]["spec"]
    all_entries = [
        entry for entry in _atomic_model_entries(session, selected_preferred=False)
        if not _is_codex_helper_atomic_model(entry["model"])
    ]
    if all_entries:
        return all_entries[0]["spec"]
    entries = _atomic_model_entries(session, selected_preferred=False)
    return entries[0]["spec"] if entries else None


_TARGET_SELECTION_CANCELLED = object()


def _primary_atomic_model_entries(session):
    return _alignment_atomic_model_entries(session)


def _selected_atomic_model_specs(session):
    specs = set()
    specs.update(_model_panel_selected_atomic_model_specs(session))
    try:
        for model in session.selection.models():
            for atomic_model in _atomic_child_structures(model):
                model_id = str(getattr(atomic_model, "id_string", "") or "")
                if model_id:
                    specs.add(f"#{model_id}")
    except Exception:
        pass
    try:
        from chimerax.atomic import selected_atoms

        for atom in selected_atoms(session):
            model = getattr(atom, "structure", None)
            model_id = str(getattr(model, "id_string", "") or "")
            if model_id:
                specs.add(f"#{model_id}")
    except Exception:
        pass
    try:
        from chimerax.atomic import selected_residues

        for residue in selected_residues(session):
            model = getattr(residue, "structure", None)
            model_id = str(getattr(model, "id_string", "") or "")
            if model_id:
                specs.add(f"#{model_id}")
    except Exception:
        pass
    return specs


def _model_panel_selected_atomic_model_specs(session):
    specs = set()
    try:
        from chimerax.atomic import AtomicStructure
        from chimerax.model_panel.tool import ModelPanel

        panel = ModelPanel.get_singleton(session)
        if panel is None:
            return specs
        selected_items = list(panel.tree.selectedItems() or [])
        if not selected_items:
            return specs
        for item in selected_items:
            try:
                model = panel.models[panel._items.index(item)]
            except Exception:
                continue
            for atomic_model in _atomic_child_structures(model, AtomicStructure):
                model_id = str(getattr(atomic_model, "id_string", "") or "")
                if model_id:
                    specs.add(f"#{model_id}")
    except Exception:
        pass
    return specs


def _model_choice_text(entry):
    model = entry.get("model")
    spec = str(entry.get("spec") or "").strip()
    name = str(entry.get("name") or "model").strip()
    try:
        chain_count = len(getattr(model, "chains", []) or [])
    except Exception:
        chain_count = 0
    try:
        residue_count = len(getattr(model, "residues", []) or [])
    except Exception:
        residue_count = 0
    try:
        atom_count = len(getattr(model, "atoms", []) or [])
    except Exception:
        atom_count = 0
    parts = [spec, name]
    counts = []
    if chain_count:
        counts.append(f"{chain_count} chain")
    if residue_count:
        counts.append(f"{residue_count} residues")
    if atom_count:
        counts.append(f"{atom_count} atoms")
    if counts:
        parts.append("(" + ", ".join(counts) + ")")
    return "  ".join(part for part in parts if part)


def _prompt_toolbar_target_model_spec(session, action_label, *, force_prompt=True):
    """Return a model spec, None when no model exists, or cancellation sentinel."""
    forced = getattr(session, "_codex_target_model_force", None)
    if forced is not None:
        try:
            delattr(session, "_codex_target_model_force")
        except Exception:
            pass
        return str(forced).strip() or None

    entries = _primary_atomic_model_entries(session)
    if not entries:
        return None
    selected_specs = _selected_atomic_model_specs(session)
    default_index = 0
    for index, entry in enumerate(entries):
        if entry.get("spec") in selected_specs:
            default_index = index
            break

    if len(entries) == 1:
        return entries[0]["spec"]
    if not force_prompt and len(selected_specs) == 1:
        selected = next((entry["spec"] for entry in entries if entry["spec"] in selected_specs), None)
        if selected:
            return selected
    if not getattr(getattr(session, "ui", None), "is_gui", False):
        return entries[default_index]["spec"]

    def show_dialog():
        try:
            from Qt.QtCore import Qt
            from Qt.QtWidgets import QDialog, QDialogButtonBox, QLabel, QListWidget, QVBoxLayout
        except Exception:
            return entries[default_index]["spec"]

        dialog = QDialog(session.ui.main_window)
        dialog.setWindowTitle(f"{action_label} target structure")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)
        layout.addWidget(
            QLabel(
                f"Multiple atomic models are open. Select which structure {action_label} should use:",
                dialog,
            )
        )
        list_widget = QListWidget(dialog)
        for entry in entries:
            list_widget.addItem(_model_choice_text(entry))
        list_widget.setCurrentRow(default_index)
        layout.addWidget(list_widget)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            Qt.Orientation.Horizontal,
            dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return _TARGET_SELECTION_CANCELLED
        row = list_widget.currentRow()
        if row < 0:
            row = default_index
        return entries[row]["spec"]

    if _is_qt_main_thread():
        return show_dialog()
    return _call_ui_thread(session, show_dialog, timeout=300)


def _prompt_toolbar_alignment_entries(session, action_label):
    """Return ordered model entries for multi-structure alignment, or cancellation sentinel."""
    entries = _alignment_atomic_model_entries(session)
    if len(entries) < 2:
        return entries
    selected_specs = _selected_atomic_model_specs(session)
    if not getattr(getattr(session, "ui", None), "is_gui", False):
        selected_entries = [entry for entry in entries if entry.get("spec") in selected_specs]
        return selected_entries if len(selected_entries) >= 2 else entries

    def show_dialog():
        try:
            from Qt.QtCore import Qt
            from Qt.QtWidgets import (
                QAbstractItemView,
                QComboBox,
                QDialog,
                QDialogButtonBox,
                QLabel,
                QListWidget,
                QVBoxLayout,
            )
        except Exception:
            return entries

        dialog = QDialog(session.ui.main_window)
        dialog.setWindowTitle(f"{action_label} structures")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"Select the reference and moving structures for {action_label}:", dialog))

        reference_combo = QComboBox(dialog)
        default_reference = 0
        for index, entry in enumerate(entries):
            reference_combo.addItem(_model_choice_text(entry))
            if entry.get("spec") in selected_specs:
                default_reference = index
        reference_combo.setCurrentIndex(default_reference)
        layout.addWidget(QLabel("Reference:", dialog))
        layout.addWidget(reference_combo)

        moving_list = QListWidget(dialog)
        moving_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for index, entry in enumerate(entries):
            moving_list.addItem(_model_choice_text(entry))
            item = moving_list.item(index)
            selected = entry.get("spec") in selected_specs if selected_specs else index != default_reference
            if index == default_reference and not selected_specs:
                selected = False
            item.setSelected(bool(selected))
        layout.addWidget(QLabel("Moving structures:", dialog))
        layout.addWidget(moving_list)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            Qt.Orientation.Horizontal,
            dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return _TARGET_SELECTION_CANCELLED
        reference_index = max(0, int(reference_combo.currentIndex()))
        ordered = [entries[reference_index]]
        for row in range(moving_list.count()):
            if row == reference_index:
                continue
            if moving_list.item(row).isSelected():
                ordered.append(entries[row])
        if len(ordered) < 2:
            return _TARGET_SELECTION_CANCELLED
        return ordered

    if _is_qt_main_thread():
        return show_dialog()
    return _call_ui_thread(session, show_dialog, timeout=300)


def _run_chimerax(session, command, executor=None):
    if executor is not None:
        return executor(command)
    from chimerax.core.commands import run

    return run(session, command)


_EXTERNAL_OUTPUT_COLORS = ("gold", "cyan", "magenta", "hotpink", "orange")
_EXTERNAL_OUTPUT_COLOR_HEX = {
    "gold": "#ffd700",
    "cyan": "#00ffff",
    "magenta": "#ff00ff",
    "hotpink": "#ff69b4",
    "orange": "#ffa500",
}
_EXTERNAL_OUTPUT_SUFFIXES = (".pdb", ".cif", ".mmcif", ".sdf", ".pse")


def _sanitize_loader_token(text, fallback="result"):
    token = re.sub(r"[^A-Za-z0-9_]+", "_", str(text or "").strip()).strip("_")
    return token or fallback


def _external_output_files(path, *, limit=10):
    root = Path(path).expanduser()
    if not root.exists():
        return []
    candidates = []
    if root.is_file():
        candidates = [root]
    else:
        for suffix in _EXTERNAL_OUTPUT_SUFFIXES:
            candidates.extend(root.rglob("*" + suffix))
    files = [
        item for item in candidates
        if item.is_file()
        and not item.name.startswith(".")
        and item.suffix.lower() in _EXTERNAL_OUTPUT_SUFFIXES
    ]
    files.sort(key=lambda item: (item.stat().st_mtime, str(item)), reverse=True)
    return files[: max(1, int(limit or 10))]


def _cleanup_external_loaded_models(session, tool):
    from .named_selection import list_groups, remove_group

    token = _sanitize_loader_token(tool, fallback="tool").lower()
    attr = f"_codex_{token}_loaded_models"
    prior_models = [model for model in getattr(session, attr, []) or [] if model is not None]
    if prior_models:
        try:
            session.models.close(prior_models)
        except Exception as err:
            session.logger.warning(f"Could not close prior {tool} loaded models: {err}")
    setattr(session, attr, [])

    prefix = token + "_"
    for name in list(list_groups(session)):
        if str(name).startswith(prefix):
            try:
                remove_group(session, name)
            except Exception as err:
                session.logger.warning(f"Could not remove prior {tool} group '{name}': {err}")


def _style_loaded_external_models(session, models, color):
    for model in models:
        spec = f"#{getattr(model, 'id_string', '?')}"
        for command in (
            f"cartoon {spec}",
            f"style {spec} stick",
            f"color {spec} {color}",
        ):
            try:
                _run_chimerax(session, command)
            except Exception:
                pass


def _open_external_outputs_on_ui(session, path, *, tool, label=None, limit=10):
    from .named_selection import add_group

    token = _sanitize_loader_token(tool, fallback="tool").lower()
    label = label or tool
    source = Path(path).expanduser()
    files = _external_output_files(source, limit=limit)
    if not files:
        return f"{label}: no PDB/CIF/SDF/PSE output files found under {source}."

    _cleanup_external_loaded_models(session, token)
    opened_models = []
    opened_paths = []
    source_token = _sanitize_loader_token(source.stem if source.is_file() else source.name, fallback="result").lower()
    for index, file_path in enumerate(files, start=1):
        before = _model_identity_set(session)
        _run_chimerax(session, "open " + _quote_command_token(str(file_path)))
        new_models = _new_models_since(session, before)
        color = _EXTERNAL_OUTPUT_COLORS[(index - 1) % len(_EXTERNAL_OUTPUT_COLORS)]
        group_name = f"{token}_{source_token}"
        if len(files) > 1:
            group_name = f"{group_name}_{index}"
        for model in new_models:
            try:
                model.name = group_name
            except Exception:
                pass
        _style_loaded_external_models(session, new_models, color)
        specs = " ".join(f"#{getattr(model, 'id_string', '?')}" for model in new_models)
        if specs:
            try:
                add_group(session, group_name, specs, color=_EXTERNAL_OUTPUT_COLOR_HEX.get(color))
            except Exception as err:
                session.logger.warning(f"Could not create {label} group '{group_name}': {err}")
        opened_models.extend(new_models)
        opened_paths.append(file_path)

    setattr(session, f"_codex_{token}_loaded_models", opened_models)
    try:
        _run_chimerax(session, "view")
    except Exception:
        pass
    return "\n".join(
        [
            f"Opened {len(opened_models)} {label} model(s) from {source}.",
            "Opened files:",
            *[f"- {item}" for item in opened_paths],
        ]
    )


def load_external_tool_outputs(session, path, *, tool, label=None, limit=10):
    if _is_qt_main_thread():
        return _open_external_outputs_on_ui(session, path, tool=tool, label=label, limit=limit)

    result_box = {}
    event = threading.Event()

    def finish():
        try:
            result_box["message"] = _open_external_outputs_on_ui(
                session,
                path,
                tool=tool,
                label=label,
                limit=limit,
            )
        except Exception as err:
            result_box["error"] = err
        finally:
            event.set()

    try:
        session.ui.thread_safe(finish)
    except Exception:
        finish()
    if not event.wait(180):
        raise TimeoutError(
            f"external loader UI bounce did not complete within 180s for tool {tool!r}"
        )
    if "error" in result_box:
        raise result_box["error"]
    return result_box.get("message", "")


def _start_external_output_watcher(session, path, *, tool, label=None, limit=10, timeout_seconds=3600, poll_seconds=5):
    root = Path(path).expanduser()
    token = _sanitize_loader_token(tool, fallback="tool").lower()
    watchers_attr = f"_codex_{token}_watchers"
    watchers = getattr(session, watchers_attr, None)
    if watchers is None:
        watchers = {}
        setattr(session, watchers_attr, watchers)
    key = str(root)
    existing = watchers.get(key)
    if existing is not None:
        stop_event = existing.get("stop")
        if stop_event is not None:
            try:
                stop_event.set()
            except Exception:
                pass

    stop_event = threading.Event()

    def watcher():
        import time

        deadline = time.time() + timeout_seconds
        last_count = 0
        stable_polls = 0
        while not stop_event.is_set():
            files = _external_output_files(root, limit=limit)
            count = len(files)
            if count > 0 and count == last_count:
                stable_polls += 1
            else:
                stable_polls = 0
            last_count = count
            if count > 0 and stable_polls >= 1:
                try:
                    message = load_external_tool_outputs(session, root, tool=tool, label=label, limit=limit)
                    if message:
                        session.logger.info(message)
                except Exception as err:
                    session.logger.warning(f"{label or tool} watcher could not load outputs from {root}: {err}")
                break
            if time.time() >= deadline:
                session.logger.warning(f"{label or tool} watcher timed out at {root}; use /{token}_load <path> to load manually.")
                break
            stop_event.wait(poll_seconds)
        try:
            current = getattr(session, watchers_attr, {})
            if current.get(key, {}).get("stop") is stop_event:
                current.pop(key, None)
        except Exception:
            pass

    thread = threading.Thread(target=watcher, daemon=True)
    watchers[key] = {"thread": thread, "stop": stop_event}
    thread.start()
    return thread


_DOWNLOADS_PATTERNS = {
    "dali": ("*.txt", "*.html", "dali*.tar.gz", "dali*.zip"),
    "vast": ("*.pdb", "*.cif", "vast*.txt", "vast*.tar.gz"),
    "pdbefold": ("pdbefold*.pdb", "pdbefold*.zip", "*_pdbefold*"),
    "usalign": ("*.pdb", "us_align*.txt", "tmscore*.txt"),
    "hpepdock": ("hpepdock*.tar.gz", "top10_models*.tar.gz", "model_*.pdb"),
    "alphafold": ("*.cif", "*.pdb", "AF-*.pdb"),
    "afcomplex": ("*.cif", "*.pdb", "fold_*.cif"),
}


def _snapshot_downloads(downloads_dir, patterns):
    snap = set()
    for pat in patterns:
        for path in downloads_dir.glob(pat):
            try:
                snap.add((path.resolve(), path.stat().st_mtime))
            except Exception:
                pass
    return snap


def _start_downloads_watcher(session, *, tool, label=None, timeout_seconds=3600, poll_seconds=30, auto_alignpanel=True):
    """Watch ~/Downloads for files matching the tool's known result patterns,
    appearing AFTER this watcher starts. When one shows up + the file size has
    settled (two consecutive polls with same mtime), open it in ChimeraX and
    optionally call /alignpanel for sequence-bar integration.

    Used by DALI/VAST/PDBeFold/US-align/HPEPDOCK web launchers so the user
    doesn't need to manually drag-drop result files into ChimeraX.
    """
    patterns = _DOWNLOADS_PATTERNS.get(tool, ("*.pdb", "*.cif"))
    label = label or tool.title()
    downloads = Path.home() / "Downloads"
    if not downloads.exists():
        return None

    baseline = _snapshot_downloads(downloads, patterns)
    watchers = getattr(session, "_codex_downloads_watchers", None)
    if watchers is None:
        watchers = {}
        session._codex_downloads_watchers = watchers
    key = f"{tool}::{int(time.time())}"

    stop_event = threading.Event()

    def watcher():
        import time as _time
        deadline = _time.time() + timeout_seconds
        announced = set()
        seen_stable = {}
        while not stop_event.is_set():
            current = _snapshot_downloads(downloads, patterns)
            new_paths = [p for p, _mt in current if (p, _mt) not in baseline]
            for path in new_paths:
                if path in announced:
                    continue
                try:
                    mt = path.stat().st_mtime
                except Exception:
                    continue
                last_mt = seen_stable.get(path)
                if last_mt is None:
                    seen_stable[path] = mt
                    continue
                if mt != last_mt:
                    seen_stable[path] = mt
                    continue
                # Stable mtime → file finished writing
                announced.add(path)
                try:
                    session.logger.info(
                        f"[{label}] auto-detected new result in ~/Downloads: {path.name} — opening in ChimeraX."
                    )
                except Exception:
                    pass
                if path.suffix.lower() in (".pdb", ".cif", ".mmcif", ".ent"):
                    # Critic P0: opening a model and align-panel registration both
                    # mutate session.models — must run on the UI thread or macOS
                    # OpenGL context will explode. Bounce via session.ui.thread_safe.
                    safe_path = _quote_command_token(str(path))
                    def _open_and_register(_path=safe_path, _label=label, _auto=auto_alignpanel):
                        try:
                            from chimerax.core.commands import run as _run
                            _run(session, f"open {_path}")
                        except Exception as err:
                            try:
                                session.logger.warning(f"[{_label}] could not open {Path(_path).name}: {err}")
                            except Exception:
                                pass
                            return
                        if _auto:
                            try:
                                from .toolbar_actions import register_open_models_in_alignment_panel
                                msg = register_open_models_in_alignment_panel(session)
                                if msg:
                                    try:
                                        session.logger.info(f"[{_label}] alignment panel: {msg}")
                                    except Exception:
                                        pass
                            except Exception as err:
                                try:
                                    session.logger.warning(f"[{_label}] alignment registration failed: {err}")
                                except Exception:
                                    pass
                    try:
                        session.ui.thread_safe(_open_and_register)
                    except Exception as err:
                        # Cannot bounce to UI thread — abort with a warning rather
                        # than risking an off-thread session.models mutation.
                        try:
                            session.logger.warning(
                                f"[{label}] auto-load aborted: cannot reach UI thread ({err}). "
                                f"Open the file manually: {path}"
                            )
                        except Exception:
                            pass
                else:
                    try:
                        session.logger.info(
                            f"[{label}] {path.name} is a non-structure file ({path.suffix}); leaving alone."
                        )
                    except Exception:
                        pass
            if _time.time() >= deadline:
                break
            stop_event.wait(poll_seconds)
        try:
            current = getattr(session, "_codex_downloads_watchers", {})
            if current.get(key, {}).get("stop") is stop_event:
                current.pop(key, None)
        except Exception:
            pass

    thread = threading.Thread(target=watcher, daemon=True, name=f"codex-dl-watch-{tool}")
    watchers[key] = {"thread": thread, "stop": stop_event, "tool": tool}
    thread.start()
    try:
        session.logger.info(
            f"[{label}] watching ~/Downloads for new results (patterns: {', '.join(patterns)}). "
            f"Auto-load + /alignpanel will fire when a matching file appears. Timeout {timeout_seconds//60} min."
        )
    except Exception:
        pass
    return thread


def load_alphafold_outputs(session, path):
    return load_external_tool_outputs(session, path, tool="alphafold", label="AlphaFold")


def load_afcomplex_outputs(session, path):
    return load_external_tool_outputs(session, path, tool="afcomplex", label="AF Complex")


def load_nucdock_outputs(session, path):
    return load_external_tool_outputs(session, path, tool="nucdock", label="NucDock")


def load_boltz_outputs(session, path):
    return load_external_tool_outputs(session, path, tool="boltz", label="Boltz")


def load_foldmason_outputs(session, path):
    return load_external_tool_outputs(session, path, tool="foldmason", label="FoldMason")


def load_folddisco_outputs(session, path):
    return load_external_tool_outputs(session, path, tool="folddisco", label="FoldDisco")


def load_dali_outputs(session, path):
    return load_external_tool_outputs(session, path, tool="dali", label="DALI")


def load_vast_outputs(session, path):
    return load_external_tool_outputs(session, path, tool="vast", label="VAST")


def load_pdbefold_outputs(session, path):
    return load_external_tool_outputs(session, path, tool="pdbefold", label="PDBeFold")


def load_usalign_outputs(session, path):
    return load_external_tool_outputs(session, path, tool="usalign", label="US-align")


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


def _export_model_files(
    session,
    *,
    fmt="mmcif",
    selected_preferred=False,
    prefix="chimerax_structures",
    executor=None,
    model_hint=None,
):
    if model_hint:
        entries = [
            entry for entry in _primary_atomic_model_entries(session)
            if entry.get("spec") == str(model_hint).strip()
        ]
        if not entries:
            entries = _atomic_model_entries(session, selected_preferred=selected_preferred)
    else:
        entries = _atomic_model_entries(session, selected_preferred=selected_preferred)
    return _export_model_entries(
        session,
        entries,
        fmt=fmt,
        prefix=prefix,
        executor=executor,
    )


def _export_first_structure_file(session, *, fmt="pdb", prefix="chimerax_structure", executor=None, model_hint=None):
    files, out_dir = _export_model_files(
        session,
        fmt=fmt,
        selected_preferred=True,
        prefix=prefix,
        executor=executor,
        model_hint=model_hint,
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


def launch_foldmason(session, *, executor=None, auto_similar=True, similar_count=5, model_hint=None):
    if model_hint:
        entries = [entry for entry in _primary_atomic_model_entries(session) if entry.get("spec") == str(model_hint).strip()]
    else:
        entries = _atomic_model_entries(session, selected_preferred=True)
    if len(entries) < 2 and not model_hint:
        all_entries = _atomic_model_entries(session, selected_preferred=False)
        if len(all_entries) >= 2:
            entries = all_entries
    if len(entries) < 2 and auto_similar:
        return launch_foldseek_foldmason(session, count=similar_count, executor=executor, model_hint=model_hint)

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


def _prompt_peptide_sequence(session):
    if not getattr(session.ui, "is_gui", False):
        return None
    from Qt.QtWidgets import QInputDialog

    text, ok = QInputDialog.getMultiLineText(
        session.ui.main_window,
        "RAPiDock Peptide",
        "Paste peptide sequence in one-letter codes.",
        "",
    )
    if not ok:
        return None
    return text


def _prompt_cavity_params(
    session,
    *,
    default_distance=5.0,
    default_transparency=65,
    default_count=5,
    default_min_volume=30.0,
    default_min_depth=1.0,
):
    """Ask the user which cavity overlays to render.

    Returns an options dict or None if cancelled. Falls back to defaults silently
    when the session has no GUI.
    """
    if not getattr(getattr(session, "ui", None), "is_gui", False):
        return {
            "distance": float(default_distance),
            "transparency": int(default_transparency),
            "count": int(default_count),
            "min_volume": float(default_min_volume),
            "min_depth": float(default_min_depth),
        }
    try:
        from Qt.QtCore import Qt
        from Qt.QtWidgets import (
            QDialog,
            QDialogButtonBox,
            QDoubleSpinBox,
            QFormLayout,
            QSpinBox,
        )
    except Exception:
        return {
            "distance": float(default_distance),
            "transparency": int(default_transparency),
            "count": int(default_count),
            "min_volume": float(default_min_volume),
            "min_depth": float(default_min_depth),
        }

    dialog = QDialog(session.ui.main_window)
    dialog.setWindowTitle("Cavity overlay parameters")
    dialog.setModal(True)
    form = QFormLayout(dialog)

    distance_spin = QDoubleSpinBox(dialog)
    distance_spin.setRange(2.0, 15.0)
    distance_spin.setDecimals(1)
    distance_spin.setSingleStep(0.5)
    distance_spin.setSuffix(" Å")
    distance_spin.setValue(float(default_distance))
    form.addRow("Contact distance:", distance_spin)

    transparency_spin = QSpinBox(dialog)
    transparency_spin.setRange(0, 100)
    transparency_spin.setSingleStep(5)
    transparency_spin.setSuffix(" %")
    transparency_spin.setValue(int(default_transparency))
    form.addRow("Surface transparency:", transparency_spin)

    count_spin = QSpinBox(dialog)
    count_spin.setRange(1, 6)
    count_spin.setSingleStep(1)
    count_spin.setSuffix(" candidate(s)")
    count_spin.setValue(max(1, min(6, int(default_count))))
    form.addRow("Rank top cavities:", count_spin)

    min_volume_spin = QDoubleSpinBox(dialog)
    min_volume_spin.setRange(1.0, 10000.0)
    min_volume_spin.setDecimals(0)
    min_volume_spin.setSingleStep(10.0)
    min_volume_spin.setSuffix(" Å³")
    min_volume_spin.setValue(float(default_min_volume))
    form.addRow("Minimum volume:", min_volume_spin)

    min_depth_spin = QDoubleSpinBox(dialog)
    min_depth_spin.setRange(0.0, 50.0)
    min_depth_spin.setDecimals(1)
    min_depth_spin.setSingleStep(0.5)
    min_depth_spin.setSuffix(" Å")
    min_depth_spin.setValue(float(default_min_depth))
    form.addRow("Minimum depth:", min_depth_spin)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        Qt.Orientation.Horizontal,
        dialog,
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)

    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return {
        "distance": float(distance_spin.value()),
        "transparency": int(transparency_spin.value()),
        "count": int(count_spin.value()),
        "min_volume": float(min_volume_spin.value()),
        "min_depth": float(min_depth_spin.value()),
    }


def _prompt_overlay_candidate_selection(
    session,
    candidates,
    *,
    title="Select overlay candidates",
    label="Select candidate(s) to show:",
    default_ranks=(1,),
    allow_multiple=True,
):
    """Return selected candidate ranks, [] for none, or None when cancelled."""
    if not candidates:
        return []
    default_set = {int(rank) for rank in (default_ranks or ())}
    fallback = sorted(default_set) if default_set else [int(candidates[0].get("rank", 1) or 1)]
    if not getattr(getattr(session, "ui", None), "is_gui", False):
        return fallback
    try:
        from Qt.QtCore import Qt
        from Qt.QtWidgets import QAbstractItemView, QDialog, QDialogButtonBox, QLabel, QListWidget, QVBoxLayout
    except Exception:
        return fallback

    dialog = QDialog(session.ui.main_window)
    dialog.setWindowTitle(str(title))
    dialog.setModal(True)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(str(label), dialog))

    list_widget = QListWidget(dialog)
    list_widget.setSelectionMode(
        QAbstractItemView.SelectionMode.MultiSelection
        if allow_multiple
        else QAbstractItemView.SelectionMode.SingleSelection
    )
    rank_by_row = []
    for candidate in candidates:
        rank = int(candidate.get("rank", len(rank_by_row) + 1) or (len(rank_by_row) + 1))
        text = str(candidate.get("choice_label") or candidate.get("title") or f"Candidate #{rank}")
        list_widget.addItem(text)
        rank_by_row.append(rank)
        item = list_widget.item(list_widget.count() - 1)
        if rank in default_set:
            item.setSelected(True)
    if not list_widget.selectedItems() and list_widget.count():
        list_widget.item(0).setSelected(True)
    layout.addWidget(list_widget)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        Qt.Orientation.Horizontal,
        dialog,
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    selected = []
    for row in range(list_widget.count()):
        if list_widget.item(row).isSelected():
            selected.append(rank_by_row[row])
    return selected


def _prompt_metal_analysis_options(session):
    defaults = {
        "mode": "review",
        "model_hint": None,
        "top_n": 5,
        "include_rcsb": True,
        "rows": 20,
        "use_kvfinder": True,
        "min_tier": "low",
    }
    if not getattr(getattr(session, "ui", None), "is_gui", False):
        return defaults
    try:
        from Qt.QtCore import Qt
        from Qt.QtWidgets import (
            QCheckBox,
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QFormLayout,
            QLineEdit,
            QSpinBox,
        )
    except Exception:
        return defaults

    dialog = QDialog(session.ui.main_window)
    dialog.setWindowTitle("Metal analysis")
    dialog.setModal(True)
    form = QFormLayout(dialog)

    mode_items = [
        ("Thorough review + 3D preview", "review"),
        ("Thorough review, then ask which site to insert", "ask_place"),
        ("Fast 3D preview only", "quick"),
        ("Evidence report only", "report"),
    ]
    mode_combo = QComboBox(dialog)
    for label, _value in mode_items:
        mode_combo.addItem(label)
    forced = getattr(session, "_codex_metal_force_mode", None)
    try:
        delattr(session, "_codex_metal_force_mode")
    except Exception:
        pass
    if forced == "ask_place":
        mode_combo.setCurrentIndex(1)
    form.addRow("Action:", mode_combo)

    target_entries = _primary_atomic_model_entries(session)
    selected_specs = _selected_atomic_model_specs(session)
    target_specs = []
    if target_entries:
        target_combo = QComboBox(dialog)
        default_target_index = 0
        for index, entry in enumerate(target_entries):
            spec = str(entry.get("spec") or "").strip()
            target_specs.append(spec)
            target_combo.addItem(_model_choice_text(entry))
            if spec in selected_specs:
                default_target_index = index
        target_combo.setCurrentIndex(default_target_index)
        form.addRow("Target:", target_combo)
        target_edit = None
    else:
        target_combo = None
        target_edit = QLineEdit(dialog)
        target_edit.setPlaceholderText("#1 or chain/model hint; blank = current open structure")
        form.addRow("Target:", target_edit)

    top_spin = QSpinBox(dialog)
    top_spin.setRange(1, 10)
    top_spin.setValue(defaults["top_n"])
    form.addRow("Candidates:", top_spin)

    tier_combo = QComboBox(dialog)
    tier_items = [
        ("Show all reviewed candidates", "low"),
        ("Medium+ confidence only", "medium"),
        ("High confidence only", "high"),
    ]
    for label, _value in tier_items:
        tier_combo.addItem(label)
    form.addRow("3D preview gate:", tier_combo)

    kv_check = QCheckBox("Use KVFinder pocket overlap", dialog)
    kv_check.setChecked(True)
    form.addRow("Pockets:", kv_check)

    rcsb_check = QCheckBox("Check RCSB experimental homologs", dialog)
    rcsb_check.setChecked(True)
    form.addRow("Fold evidence:", rcsb_check)

    rows_spin = QSpinBox(dialog)
    rows_spin.setRange(4, 40)
    rows_spin.setValue(defaults["rows"])
    form.addRow("RCSB hits:", rows_spin)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        Qt.Orientation.Horizontal,
        dialog,
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)

    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    mode = mode_items[mode_combo.currentIndex()][1]
    min_tier = tier_items[tier_combo.currentIndex()][1]
    if target_combo is not None:
        index = max(0, int(target_combo.currentIndex()))
        model_hint = target_specs[index] if index < len(target_specs) else None
    else:
        model_hint = str(target_edit.text() or "").strip() or None
    return {
        "mode": mode,
        "model_hint": model_hint,
        "top_n": int(top_spin.value()),
        "include_rcsb": bool(rcsb_check.isChecked()),
        "rows": int(rows_spin.value()),
        "use_kvfinder": bool(kv_check.isChecked()),
        "min_tier": min_tier,
    }


def _prompt_metal_candidate_choice(session, candidates):
    if not candidates or not getattr(getattr(session, "ui", None), "is_gui", False):
        return None
    try:
        from Qt.QtWidgets import QInputDialog
    except Exception:
        return None

    items = ["Keep preview only"]
    lookup = {}
    for cand in candidates[:10]:
        idx = int(cand.get("site_index", len(items)))
        residues = ", ".join(spec for spec, _name in (cand.get("residues") or [])[:4])
        label = (
            f"Site {idx}: {cand.get('best_metal')} "
            f"{cand.get('review_tier', 'candidate')} "
            f"review {float(cand.get('review_score', 0.0) or 0.0):.2f} "
            f"({residues})"
        )
        items.append(label)
        lookup[label] = idx
    choice, ok = QInputDialog.getItem(
        session.ui.main_window,
        "Insert reviewed metal",
        "Select candidate site:",
        items,
        0,
        False,
    )
    if not ok:
        return None
    return lookup.get(choice)


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


def launch_nucleotide_docking_pipeline(session, request_text=None, *, executor=None, model_hint=None):
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
        model_hint=model_hint,
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


def launch_nucleotide_alphafold_pipeline(session, request_text=None, model_hint=None):
    if not str(request_text or "").strip():
        request_text = _prompt_nucleotide_sequence(session)
    sequence, requested = _parse_nucleotide_request(request_text)
    if not sequence:
        return "No nucleotide sequence was provided."
    invalid = sorted(set(sequence) - set("ACGTU"))
    if invalid:
        return f"Nucleotide sequence contains unsupported letters: {''.join(invalid)}"
    protein_entries = _protein_chain_entries(session, model_hint=model_hint)
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
    if not event.wait(60):
        raise TimeoutError("Boltz panel UI bounce did not complete within 60s")
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


def _boltz_sequence_entries(session, model_hint=None):
    from chimerax.atomic import AtomicStructure, Residue
    from .semantic import resolve_model_spec

    resolved_hint = resolve_model_spec(session, model_hint) if model_hint else None
    entries = []
    used_ids = set()
    next_id = ord("A")
    for model in session.models.list():
        if not isinstance(model, AtomicStructure):
            continue
        model_spec = f"#{getattr(model, 'id_string', '?')}"
        if resolved_hint and model_spec != resolved_hint:
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
    return load_boltz_outputs(session, output_dir)


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


def launch_boltz_latest_predict(session, *, executable=None, model="boltz2", model_hint=None):
    entries = _boltz_sequence_entries(session, model_hint=model_hint)
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


def _rapidock_sequence_entries(session, model_hint=None):
    return _boltz_sequence_entries(session, model_hint=model_hint)


def _rapidock_repo_root():
    candidates = []
    if RAPIDOCK_DEFAULT_REPO:
        candidates.append(RAPIDOCK_DEFAULT_REPO)
    home = Path.home()
    candidates.extend(
        [
            str(home / "RAPiDock"),
            str(home / "rapidock"),
            str(home / "Downloads" / "RAPiDock"),
        ]
    )
    for candidate in candidates:
        if not candidate:
            continue
        root = Path(candidate).expanduser()
        if (root / "inference.py").exists():
            return root.resolve()
    return None


def _rapidock_python_executable(repo_root):
    root = Path(repo_root).expanduser()
    for relative in (".venv/bin/python", "venv/bin/python"):
        candidate = root / relative
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return sys.executable


def _rapidock_pyrosetta_available(python_exe):
    try:
        result = subprocess.run(
            [python_exe, "-c", "import pyrosetta"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            env=_clean_subprocess_env(),
        )
    except Exception:
        return False
    return result.returncode == 0


def _prompt_rapidock_save_location(session, default_root):
    if not getattr(getattr(session, "ui", None), "is_gui", False):
        return Path(default_root).expanduser()
    default_path = Path(default_root).expanduser()
    default_path.mkdir(parents=True, exist_ok=True)

    if not _is_qt_main_thread():
        result_box = {}
        event = threading.Event()

        def runner():
            try:
                result_box["value"] = _prompt_rapidock_save_location(session, default_path)
            except Exception as err:
                result_box["error"] = err
            finally:
                event.set()

        try:
            session.ui.thread_safe(runner)
        except Exception as err:
            try:
                session.logger.warning(
                    f"RAPiDock save-folder dialog could not bounce to main thread ({err}); using {default_path}"
                )
            except Exception:
                pass
            return default_path
        # Folder picker is user-driven (no upper bound on dialog open time);
        # use a 30-min cap so a never-closed dialog at app shutdown doesn't
        # leak the worker thread forever. Logs the timeout and falls back.
        if not event.wait(1800):
            try:
                session.logger.warning(
                    f"RAPiDock save-folder dialog timed out after 30 min; using {default_path}"
                )
            except Exception:
                pass
            return default_path
        if "error" in result_box:
            try:
                session.logger.warning(
                    f"RAPiDock save-folder dialog failed ({result_box['error']}); using {default_path}"
                )
            except Exception:
                pass
            return default_path
        return result_box.get("value", default_path)

    try:
        from Qt.QtWidgets import QFileDialog
    except Exception:
        return default_path
    try:
        options = QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontUseNativeDialog
    except Exception:
        try:
            options = QFileDialog.ShowDirsOnly | QFileDialog.DontUseNativeDialog
        except Exception:
            options = QFileDialog.Option(0)
    try:
        chosen = QFileDialog.getExistingDirectory(
            session.ui.main_window,
            "RAPiDock — choose folder to save predictions",
            str(default_path),
            options,
        )
    except Exception as err:
        try:
            session.logger.warning(
                f"RAPiDock save-folder dialog unavailable ({err}); using default {default_path}"
            )
        except Exception:
            pass
        return default_path
    if not chosen:
        return None
    return Path(chosen).expanduser()


_RAPIDOCK_CHECKPOINT_URLS = {
    "rapidock_global.pt": "https://zenodo.org/records/14193621/files/rapidock_global.pt?download=1",
    "rapidock_local.pt": "https://zenodo.org/records/14193621/files/rapidock_local.pt?download=1",
}
_RAPIDOCK_CORE_PIP = (
    "torch==2.2.0",
    "MDAnalysis==2.6.1",
    "pyyaml",
    "e3nn==0.5.1",
    "fair-esm==2.0.0",
    "rdkit-pypi==2022.9.5",
    "biopython==1.84",
    "pandas==2.1.0",
    "networkx==3.2.1",
    "torch_geometric==2.5.0",
)
_RAPIDOCK_PYG_WHEELS = (
    "torch_scatter",
    "torch_sparse",
    "torch_cluster",
    "torch_spline_conv",
)
_RAPIDOCK_PYG_INDEX = "https://data.pyg.org/whl/torch-2.2.0+cpu.html"
_RAPIDOCK_PYG_INDEX_GPU = {
    "cu118": "https://data.pyg.org/whl/torch-2.2.0+cu118.html",
    "cu121": "https://data.pyg.org/whl/torch-2.2.0+cu121.html",
}
_RAPIDOCK_TORCH_GPU_INDEX = {
    "cu118": "https://download.pytorch.org/whl/cu118",
    "cu121": "https://download.pytorch.org/whl/cu121",
}


def _detect_cuda_tag(session=None):
    """Detect installed CUDA version, return one of {'cu118','cu121'} or None.

    Tries `nvidia-smi --query-gpu=driver_version,cuda_version` first, then
    parses the legacy `nvidia-smi` text output, then falls back to `nvcc --version`.
    """
    import re as _re

    candidates = []
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            candidates.append(("nvidia-smi", result.stdout.strip()))
    except Exception:
        pass

    cuda_runtime = None
    try:
        result = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            match = _re.search(r"CUDA Version[:\s]+(\d+)\.(\d+)", result.stdout)
            if match:
                cuda_runtime = (int(match.group(1)), int(match.group(2)))
                candidates.append(("nvidia-smi-text", f"{cuda_runtime[0]}.{cuda_runtime[1]}"))
    except Exception:
        pass

    if cuda_runtime is None:
        try:
            result = subprocess.run(["nvcc", "--version"], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                match = _re.search(r"release\s+(\d+)\.(\d+)", result.stdout)
                if match:
                    cuda_runtime = (int(match.group(1)), int(match.group(2)))
                    candidates.append(("nvcc", f"{cuda_runtime[0]}.{cuda_runtime[1]}"))
        except Exception:
            pass

    if not candidates:
        if session is not None:
            try:
                session.logger.info("[RAPiDock setup] No NVIDIA GPU detected — falling back to CPU build.")
            except Exception:
                pass
        return None

    if cuda_runtime is None:
        return None
    major, minor = cuda_runtime
    if major >= 12:
        return "cu121"
    if major == 11 and minor >= 6:
        return "cu118"
    if session is not None:
        try:
            session.logger.warning(
                f"[RAPiDock setup] Detected CUDA {major}.{minor} — too old for prebuilt PyTorch 2.2 wheels."
                " Falling back to CPU build."
            )
        except Exception:
            pass
    return None


def _prompt_rapidock_engine(session):
    """One-time per-session engine picker. Persists choice in `_codex_rapidock_engine`.

    Returns one of 'auto'|'native'|'docker'|'hpepdock' or None on cancel.
    """
    cached = getattr(session, "_codex_rapidock_engine", None)
    if cached:
        return cached
    if not getattr(getattr(session, "ui", None), "is_gui", False):
        env_choice = (os.environ.get("RAPIDOCK_ENGINE") or "").strip().lower()
        if env_choice in RAPIDOCK_ENGINES:
            session._codex_rapidock_engine = env_choice
            return env_choice
        session._codex_rapidock_engine = "auto"
        return "auto"
    if not _is_qt_main_thread():
        result_box = {}
        event = threading.Event()

        def runner():
            try:
                result_box["value"] = _prompt_rapidock_engine(session)
            except Exception as err:
                result_box["error"] = err
            finally:
                event.set()

        try:
            session.ui.thread_safe(runner)
        except Exception:
            return "auto"
        # User-driven engine picker dialog; cap at 30 min so a never-closed
        # dialog can't leak the worker thread.
        if not event.wait(1800):
            return "auto"
        return result_box.get("value", "auto")

    try:
        from Qt.QtWidgets import QInputDialog
    except Exception:
        return "auto"
    options = [
        "Auto (Mac → HPEPDOCK web, Linux → native)",
        "HPEPDOCK web service (any OS, no GPU, ~5–15 min)",
        "Docker (linux/amd64, requires Docker Desktop, slow on Mac)",
        "Native venv inference (Linux + GPU recommended)",
    ]
    chosen, ok = QInputDialog.getItem(
        session.ui.main_window,
        "RAPiDock engine",
        "Pick the docking backend (this choice is remembered for the session):",
        options, 0, False,
    )
    if not ok:
        return None
    if chosen.startswith("Auto"):
        engine = "auto"
    elif chosen.startswith("HPEPDOCK"):
        engine = "hpepdock"
    elif chosen.startswith("Docker"):
        engine = "docker"
    else:
        engine = "native"
    session._codex_rapidock_engine = engine
    return engine


def _hpepdock_email_from_session(session):
    """Persist HPEPDOCK email between toolbar clicks within a session."""
    return getattr(session, "_codex_hpepdock_email", "") if session is not None else ""


def _prompt_hpepdock_email(session):
    """Deprecated: never prompts. Returns env var or placeholder so nothing
    in the launch path can ever return an empty string and cancel the run.
    """
    return (
        os.environ.get("HPEPDOCK_EMAIL", "").strip()
        or "noreply@chimerax-codex-bridge.local"
    )


def _check_docker_memory(session, *, min_gib=12):
    """Critic P2: warn if Docker Desktop has less than min_gib of RAM allocated."""
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.MemTotal}}"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    raw = result.stdout.strip()
    try:
        mem_bytes = int(raw)
    except Exception:
        return None
    mem_gib = mem_bytes / (1024 ** 3)
    if mem_gib < float(min_gib):
        return (
            f"Docker daemon reports only {mem_gib:.1f} GiB of RAM — RAPiDock + ESM-2 needs ~7 GiB. "
            f"Raise the limit (Docker Desktop → Settings → Resources → Memory ≥ {min_gib} GB) or expect SIGKILL."
        )
    return None


def _looks_like_chimerax_python(python_path):
    """Detect ChimeraX's bundled Python (which symlinks to the ChimeraX binary)."""
    try:
        real = os.path.realpath(str(python_path))
    except Exception:
        return False
    base = os.path.basename(real).lower()
    return "chimerax" in real.lower() or base in {"chimerax"}


def _find_system_python(session=None):
    """Locate a non-ChimeraX system Python suitable for creating the RAPiDock venv.

    RAPiDock requires Python 3.9–3.11 (PyG cpu wheel index has cp39/cp310/cp311 builds).
    Returns the best available interpreter path, or None if no suitable one is found.
    """
    candidates = []
    for env_name in ("RAPIDOCK_PYTHON", "PYTHON3", "PYTHON"):
        value = os.environ.get(env_name)
        if value:
            candidates.append(value)
    candidates.extend([
        "/opt/homebrew/bin/python3.11",
        "/opt/homebrew/bin/python3.10",
        "/opt/homebrew/bin/python3.9",
        "/usr/local/bin/python3.11",
        "/usr/local/bin/python3.10",
        "/usr/local/bin/python3.9",
        str(Path.home() / "miniforge3" / "bin" / "python3"),
        "/usr/bin/python3",
    ])
    seen = set()
    for path in candidates:
        if not path or path in seen:
            continue
        seen.add(path)
        if not (os.path.isfile(path) and os.access(path, os.X_OK)):
            continue
        if _looks_like_chimerax_python(path):
            continue
        try:
            res = subprocess.run(
                [path, "-c", "import sys; print(sys.version_info[:3])"],
                capture_output=True, text=True, timeout=10,
            )
        except Exception:
            continue
        if res.returncode != 0:
            continue
        text = res.stdout.strip()
        if "(3, 9," in text or "(3, 10," in text or "(3, 11," in text:
            if session is not None:
                try:
                    session.logger.info(f"[RAPiDock setup] Using system Python: {path} {text}")
                except Exception:
                    pass
            return path
    if session is not None:
        try:
            session.logger.warning(
                "[RAPiDock setup] No system Python 3.9/3.10/3.11 found. "
                "Install one (e.g. `brew install python@3.11`) and rerun /rapidock_setup."
            )
        except Exception:
            pass
    return None


def _clean_subprocess_env(extra=None):
    """Return os.environ copy with ChimeraX-specific Python vars stripped."""
    env = dict(os.environ)
    for key in (
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONNOUSERSITE",
        "PYTHONSTARTUP",
        "PYTHONEXECUTABLE",
        "PYTHONUSERBASE",
    ):
        env.pop(key, None)
    if extra:
        env.update(extra)
    return env


def _setup_log(session, message):
    if session is not None:
        try:
            session.logger.info(f"[RAPiDock setup] {message}")
            session.logger.status(f"[RAPiDock setup] {message}")
        except Exception:
            pass


def _run_setup_step(session, label, command, cwd=None, timeout=1800):
    _setup_log(session, f"{label} ...")
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_clean_subprocess_env(),
        )
    except subprocess.TimeoutExpired:
        return False, f"{label} timed out after {timeout}s"
    except Exception as err:
        return False, f"{label} failed to launch: {err}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()[-12:]
        return False, f"{label} exited {result.returncode}\n  " + "\n  ".join(detail)
    return True, ""


def _download_rapidock_checkpoint(session, url, target_path):
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists() and target_path.stat().st_size > 50_000_000:
        return True, f"already present ({target_path.stat().st_size // (1024 * 1024)} MB)"
    _setup_log(session, f"Downloading {target_path.name} from Zenodo (~54 MB) ...")
    try:
        from urllib.request import Request, urlopen

        req = Request(url, headers={"User-Agent": "ChimeraX-CodexBridge/1.0"})
        with urlopen(req, timeout=300) as response, target_path.open("wb") as fh:
            chunk = response.read(1024 * 256)
            while chunk:
                fh.write(chunk)
                chunk = response.read(1024 * 256)
    except Exception as err:
        return False, f"download failed: {err}"
    if target_path.stat().st_size < 50_000_000:
        target_path.unlink(missing_ok=True)
        return False, "downloaded file looked too small (<50 MB), removed"
    return True, f"saved ({target_path.stat().st_size // (1024 * 1024)} MB)"


def setup_rapidock_repo(path=None, *, session=None, skip_models=False, gpu=None):
    """Fully automated RAPiDock environment provisioning.

    Args:
        path: target directory for the clone (default ~/RAPiDock).
        session: ChimeraX session, used for streaming log status.
        skip_models: if True, skip the ~110 MB Zenodo checkpoint download.
        gpu: GPU build mode. ``None`` ⇒ CPU build. ``"auto"`` ⇒ detect via
            nvidia-smi/nvcc (falls back to CPU if no NVIDIA GPU). Or pass an
            explicit tag like ``"cu121"`` / ``"cu118"`` to force a specific
            PyTorch CUDA wheel.

    Steps:
      1. git clone RAPiDock to ~/RAPiDock (skip if already populated)
      2. python -m venv .venv inside the repo (skip if exists)
      3. pip install torch 2.2.0 (cpu or cu118/cu121 wheel)
      4. pip install core deps (MDAnalysis, e3nn, esm, rdkit, ...)
      5. pip install PyG cluster/scatter/sparse from matching PyG wheel index
      6. Download rapidock_global.pt + rapidock_local.pt from Zenodo into
         train_models/CGTensorProductEquivariantModel/
      7. import sanity check

    Returns a multi-line status string. Streams progress through session.logger
    when provided. Designed to be called from a worker thread.
    """
    import platform as _platform

    target = Path(path or "~/RAPiDock").expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)

    # Critic P8: prevent concurrent setup runs by writing a PID lock file.
    lock_path = target / ".setup.lock"
    try:
        if lock_path.exists():
            try:
                pid_text = lock_path.read_text().strip().splitlines()[0]
                pid = int(pid_text)
            except Exception:
                pid = None
            if pid is not None:
                still_alive = False
                try:
                    os.kill(pid, 0)
                    still_alive = True
                except OSError:
                    still_alive = False
                if still_alive:
                    return (
                        f"RAPiDock setup already running (PID {pid}). "
                        f"Wait for it to finish, or remove {lock_path} if it crashed."
                    )
            try:
                lock_path.unlink()
            except Exception:
                pass
        target.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(f"{os.getpid()}\n{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    except Exception:
        pass

    cuda_tag = None
    if gpu is None or str(gpu).lower() in {"cpu", "none", "false"}:
        cuda_tag = None
    elif str(gpu).lower() == "auto":
        cuda_tag = _detect_cuda_tag(session)
    elif str(gpu).lower() in _RAPIDOCK_PYG_INDEX_GPU:
        cuda_tag = str(gpu).lower()
    else:
        return (
            f"RAPiDock setup aborted: unknown gpu spec '{gpu}'. "
            f"Use one of: 'auto', 'cpu', {sorted(_RAPIDOCK_PYG_INDEX_GPU.keys())}."
        )

    build_label = f"GPU build ({cuda_tag})" if cuda_tag else "CPU build"
    log_lines = [
        f"RAPiDock setup target: {target}",
        f"Build mode: {build_label}",
    ]

    # 1) git clone
    if target.exists() and (target / "inference.py").exists():
        log_lines.append(f"Repo already present, skipping clone.")
    else:
        if target.exists() and any(target.iterdir()):
            return (
                f"RAPiDock setup aborted: {target} exists but is not a RAPiDock checkout."
                f" Move it aside or pass another path."
            )
        ok, err = _run_setup_step(
            session,
            "git clone RAPiDock",
            ["git", "clone", "--depth", "1", RAPIDOCK_GITHUB_URL, str(target)],
            timeout=600,
        )
        if not ok:
            return "RAPiDock setup failed at git clone:\n" + err
        log_lines.append(f"Cloned to {target}.")

    # 2) venv (must use real system Python, NOT ChimeraX's bundled python)
    venv_dir = target / ".venv"
    venv_python = venv_dir / "bin" / "python"

    venv_is_broken = False
    if venv_python.exists():
        if _looks_like_chimerax_python(venv_python):
            venv_is_broken = True
            _setup_log(session, "Existing venv was created with ChimeraX's bundled Python — wiping and rebuilding.")
            try:
                import shutil as _shutil
                _shutil.rmtree(venv_dir)
            except Exception as err:
                return f"RAPiDock setup failed: could not remove broken venv at {venv_dir}: {err}"

    if venv_python.exists() and os.access(venv_python, os.X_OK) and not venv_is_broken:
        log_lines.append("venv already present, skipping creation.")
    else:
        system_python = _find_system_python(session)
        if system_python is None:
            return (
                "RAPiDock setup failed: no system Python 3.9/3.10/3.11 found.\n"
                "Install one and retry. macOS: `brew install python@3.11`. "
                "Linux: `sudo apt install python3.11 python3.11-venv`."
            )
        ok, err = _run_setup_step(
            session,
            f"create .venv via {system_python}",
            [system_python, "-m", "venv", str(venv_dir)],
            timeout=120,
        )
        if not ok:
            return "RAPiDock setup failed at venv creation:\n" + err
        log_lines.append(f"venv created at {venv_dir} (using {system_python}).")

    # 3) pip install core
    ok, err = _run_setup_step(
        session,
        "upgrade pip in venv",
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "-q"],
        timeout=300,
    )
    if not ok:
        return "RAPiDock setup failed at pip upgrade:\n" + err
    if cuda_tag is not None:
        torch_index = _RAPIDOCK_TORCH_GPU_INDEX[cuda_tag]
        ok, err = _run_setup_step(
            session,
            f"install torch 2.2.0 + GPU build ({cuda_tag})",
            [
                str(venv_python), "-m", "pip", "install", "-q",
                "torch==2.2.0", "--index-url", torch_index,
            ],
            timeout=1800,
        )
        if not ok:
            return f"RAPiDock setup failed at torch GPU install:\n" + err
        log_lines.append(f"torch 2.2.0 + {cuda_tag} installed.")
        non_torch_core = tuple(pkg for pkg in _RAPIDOCK_CORE_PIP if not pkg.startswith("torch=="))
    else:
        non_torch_core = _RAPIDOCK_CORE_PIP

    ok, err = _run_setup_step(
        session,
        f"install core deps ({len(non_torch_core)} packages)",
        [str(venv_python), "-m", "pip", "install", "-q", *non_torch_core],
        timeout=1800,
    )
    if not ok:
        return "RAPiDock setup failed at core dep install:\n" + err
    log_lines.append("Core Python deps installed.")

    # 4) PyG wheels (cpu or matching cuda)
    pyg_index = _RAPIDOCK_PYG_INDEX_GPU.get(cuda_tag) if cuda_tag else _RAPIDOCK_PYG_INDEX
    pyg_label = f"GPU ({cuda_tag})" if cuda_tag else "CPU"
    arch = _platform.machine()
    if arch in ("x86_64", "AMD64", "arm64", "aarch64"):
        ok, err = _run_setup_step(
            session,
            f"install PyG {pyg_label} wheels (cluster/scatter/sparse/spline)",
            [
                str(venv_python), "-m", "pip", "install", "-q",
                "-f", pyg_index, *_RAPIDOCK_PYG_WHEELS,
            ],
            timeout=900,
        )
        if not ok:
            log_lines.append(
                f"PyG wheel install failed (proceeding anyway — RAPiDock inference will likely error):\n"
                + err
            )
        else:
            log_lines.append(f"PyG cluster/scatter/sparse installed ({pyg_label} wheels).")
    else:
        log_lines.append(f"Skipping PyG wheels (architecture {arch} not in pyg.org index).")

    # 5) Model checkpoints
    if skip_models:
        log_lines.append("Model checkpoint download skipped (skip_models=True).")
    else:
        ckpt_dir = target / "train_models" / "CGTensorProductEquivariantModel"
        for name, url in _RAPIDOCK_CHECKPOINT_URLS.items():
            ok, msg = _download_rapidock_checkpoint(session, url, ckpt_dir / name)
            if ok:
                log_lines.append(f"  {name}: {msg}")
            else:
                log_lines.append(f"  {name}: FAILED — {msg}")

    # 6) Sanity import test
    ok, err = _run_setup_step(
        session,
        "verify torch+PyG import in venv",
        [
            str(venv_python),
            "-c",
            (
                "import torch, torch_cluster, torch_scatter, torch_sparse, torch_geometric, e3nn, esm, rdkit, MDAnalysis;"
                " print('imports OK')"
            ),
        ],
        timeout=180,
    )
    if not ok:
        log_lines.append("Import sanity check FAILED:\n" + err)
    else:
        log_lines.append("Import sanity check passed.")

    try:
        if lock_path.exists():
            lock_path.unlink()
    except Exception:
        pass

    log_lines.append("")
    log_lines.append(
        f"Setup complete ({build_label}). Click the RAPiDock toolbar button to start docking."
    )
    return "\n".join(log_lines)


def _write_rapidock_virtual_screening_csv(entries, peptide, csv_path, *, protein_description=None):
    rows = [
        {
            "complex_name": "codex_rapidock_case",
            "protein_description": protein_description or entries[0]["sequence"],
            "peptide_description": peptide,
        }
    ]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["complex_name", "protein_description", "peptide_description"])
        writer.writeheader()
        writer.writerows(rows)


def _crop_receptor_around_pocket(session, pocket, output_path, *, buffer=12.0):
    """Crop the receptor to a cube around the pocket lining + buffer (Å).

    Returns (cropped_pdb_path, kept_residue_count, descriptor_dict) on success or
    (None, 0, error_string) on failure. Uses ChimeraX `save` with an atom-spec
    so coordinates remain in the original frame — RAPiDock output poses load
    aligned to the full receptor without realignment.
    """
    import numpy as _np

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lining_residues = list(pocket.get("lining_residues") or [])
    if not lining_residues:
        return None, 0, "pocket has no lining residues"
    structure = pocket.get("model")
    if structure is None:
        return None, 0, "pocket has no model attached"

    coords = []
    for residue in lining_residues:
        for atom in getattr(residue, "atoms", []):
            element = str(getattr(getattr(atom, "element", None), "name", "")).upper()
            if element == "H":
                continue
            coords.append(atom.scene_coord)
    if not coords:
        return None, 0, "no heavy atoms found in pocket lining"
    arr = _np.asarray(coords, dtype=float)
    cmin = arr.min(axis=0) - float(buffer)
    cmax = arr.max(axis=0) + float(buffer)

    keep_residue_specs = []
    for residue in getattr(structure, "residues", []):
        for atom in getattr(residue, "atoms", []):
            element = str(getattr(getattr(atom, "element", None), "name", "")).upper()
            if element == "H":
                continue
            xyz = atom.scene_coord
            if (cmin[0] <= xyz[0] <= cmax[0]
                    and cmin[1] <= xyz[1] <= cmax[1]
                    and cmin[2] <= xyz[2] <= cmax[2]):
                chain_id = str(getattr(residue, "chain_id", "")).strip() or "?"
                number = getattr(residue, "number", None)
                if number is None:
                    break
                keep_residue_specs.append(f"#{structure.id_string}/{chain_id}:{int(number)}")
                break
    if not keep_residue_specs:
        return None, 0, "no residues fell inside the buffered cube"

    spec = " ".join(keep_residue_specs)
    try:
        from chimerax.core.commands import run as _run

        _run(
            session,
            f"save {_quote_command_token(str(output_path))} relModel #{structure.id_string} {spec} format pdb",
        )
    except Exception:
        try:
            from chimerax.core.commands import run as _run

            _run(
                session,
                f"save {_quote_command_token(str(output_path))} {spec} format pdb",
            )
        except Exception as err:
            return None, 0, f"save failed: {err}"

    if not output_path.exists() or output_path.stat().st_size == 0:
        return None, 0, "cropped PDB was not written"

    return (
        output_path,
        len(keep_residue_specs),
        {
            "buffer": float(buffer),
            "kept_residues": len(keep_residue_specs),
            "cube_min": [float(v) for v in cmin],
            "cube_max": [float(v) for v in cmax],
            "pocket_index": pocket.get("index"),
            "pocket_volume": pocket.get("volume"),
            "pocket_tags": pocket.get("tags") or [],
        },
    )


_RAPIDOCK_POSE_COLORS = ("gold", "cyan", "magenta", "hotpink", "orange")
_RAPIDOCK_POSE_COLOR_HEX = {
    "gold": "#ffd700",
    "cyan": "#00ffff",
    "magenta": "#ff00ff",
    "hotpink": "#ff69b4",
    "orange": "#ffa500",
}


def _rapidock_pose_sort_key(path):
    path = Path(path)
    name = path.stem.lower()
    rank_match = re.search(r"(?:^|[_-])rank[_-]?(\d+)", name)
    if rank_match:
        return (0, int(rank_match.group(1)), path.name.lower())
    sample_match = re.search(r"(?:^|[_-])sample[_-]?(\d+)(?:[_-](\d+))?", name)
    if sample_match:
        return (
            1,
            int(sample_match.group(1)),
            int(sample_match.group(2) or 0),
            0,
            path.name.lower(),
        )
    numbers = [int(item) for item in re.findall(r"\d+", name)]
    numbers = (numbers + [999999, 999999, 999999])[:3]
    return (2, numbers[0], numbers[1], numbers[2], path.name.lower())


_RAPIDOCK_NON_POSE_SUFFIX_TOKENS = ("_reverseprocess", "_protein_raw", "_protein_processed")


def _rapidock_pose_rank(path):
    name = Path(path).stem.lower()
    rank_match = re.search(r"(?:^|[_-])rank[_-]?(\d+)", name)
    if rank_match:
        return int(rank_match.group(1))
    model_match = re.search(r"(?:^|[_-])model[_-]?(\d+)", name)
    if model_match:
        return int(model_match.group(1))
    return None


def _rapidock_is_ranked_pose(path):
    name = path.stem.lower()
    if not (
        re.search(r"(?:^|[_-])rank[_-]?\d+", name)
        or re.search(r"(?:^|[_-])model[_-]?\d+", name)
    ):
        return False
    if name.endswith("_minimized_peptide"):
        return False
    return not any(token in name for token in _RAPIDOCK_NON_POSE_SUFFIX_TOKENS)


def _rapidock_pose_preference(path):
    name = Path(path).stem.lower()
    if name.endswith("_minimized_complex"):
        return 0
    if name.endswith("_complex"):
        return 1
    return 2


def _hpepdock_validation_order(root):
    root = Path(root).expanduser()
    tables = []
    for name in ("codex_hpepdock_recommended.tsv", "codex_hpepdock_validation.tsv"):
        tables.extend(sorted(root.rglob(name), key=lambda item: (len(item.parts), str(item))))
    order = {}
    for table in tables:
        try:
            with table.open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle, delimiter="\t"):
                    rank_text = str(row.get("validation_rank") or "").strip()
                    if not rank_text:
                        continue
                    try:
                        validation_rank = int(float(rank_text))
                    except Exception:
                        continue
                    for key in ("complex_pdb", "raw_complex_pdb"):
                        path_text = str(row.get(key) or "").strip()
                        if not path_text:
                            continue
                        try:
                            order[str(Path(path_text).expanduser().resolve())] = validation_rank
                        except Exception:
                            order[str(Path(path_text).expanduser())] = validation_rank
        except Exception:
            continue
    return order


def _rapidock_output_files(output_dir, *, limit=5):
    root = Path(output_dir).expanduser()
    candidates = []
    for suffix in ("*.pdb", "*.sdf"):
        for path in root.rglob(suffix):
            if not path.is_file() or path.name.startswith("."):
                continue
            if not _rapidock_is_ranked_pose(path):
                continue
            candidates.append(path)
    by_rank = {}
    unranked = []
    for path in candidates:
        rank = _rapidock_pose_rank(path)
        if rank is None:
            unranked.append(path)
            continue
        prior = by_rank.get(rank)
        if prior is None or _rapidock_pose_preference(path) < _rapidock_pose_preference(prior):
            by_rank[rank] = path
    if by_rank:
        candidates = list(by_rank.values()) + unranked
    validation_order = _hpepdock_validation_order(root)
    if validation_order:
        def validation_key(path):
            try:
                key = str(Path(path).resolve())
            except Exception:
                key = str(path)
            return (validation_order.get(key, 999999), _rapidock_pose_sort_key(path))
        return sorted(candidates, key=validation_key)[:limit]
    return sorted(candidates, key=_rapidock_pose_sort_key)[:limit]


def _hpepdock_existing_package_from_files(output_dir, *, limit=5):
    root = Path(output_dir).expanduser()
    summary_tsv = root / "codex_hpepdock_summary.tsv"
    summaries = []

    if summary_tsv.exists():
        try:
            with summary_tsv.open(newline="", encoding="utf-8") as handle:
                summaries = [dict(row) for row in csv.DictReader(handle, delimiter="\t")]
        except Exception:
            summaries = []

    if not summaries:
        peptide_files = sorted(root.rglob("rank*_minimized_peptide.pdb"), key=_rapidock_pose_sort_key)
        for path in peptide_files[: max(1, int(limit or 5))]:
            rank = _rapidock_pose_rank(path) or len(summaries) + 1
            summaries.append({
                "validation_rank": str(len(summaries) + 1),
                "validation_status": "pose",
                "recommended": "yes" if not summaries else "no",
                "rank": str(rank),
                "pose_pdb": str(path),
                "minimized_peptide_pdb": str(path),
            })

    if not summaries:
        return {}

    receptor = None
    receptor_candidates = [
        root.parent.parent / "input" / "receptor_cropped.pdb",
        root.parent / "input" / "receptor_cropped.pdb",
        root / "receptor_cropped.pdb",
        root / "receptor.pdb",
    ]
    for candidate in receptor_candidates:
        if candidate.exists():
            receptor = str(candidate)
            break

    return {
        "receptor": receptor,
        "summaries": summaries,
        "summary_tsv": str(summary_tsv) if summary_tsv.exists() else None,
        "summary_md": str(root / "codex_hpepdock_summary.md") if (root / "codex_hpepdock_summary.md").exists() else None,
        "validation_tsv": str(root / "codex_hpepdock_validation.tsv") if (root / "codex_hpepdock_validation.tsv").exists() else None,
        "recommended_tsv": str(root / "codex_hpepdock_recommended.tsv") if (root / "codex_hpepdock_recommended.tsv").exists() else None,
        "best_report_md": str(root / "codex_hpepdock_best_pose.md") if (root / "codex_hpepdock_best_pose.md").exists() else None,
    }


def _hpepdock_summary_rank(item, fallback=999999):
    try:
        return int(float(item.get("validation_rank") or fallback))
    except Exception:
        return fallback


def _hpepdock_pose_rank_label(item):
    try:
        return int(float(item.get("rank") or 0))
    except Exception:
        return item.get("rank") or "?"


def _sanitize_rapidock_group_token(text):
    token = re.sub(r"[^A-Za-z0-9_]+", "_", str(text or "").strip().upper()).strip("_")
    return token or "peptide"


def _infer_rapidock_peptide(output_dir, pose_files=None):
    root = Path(output_dir).expanduser()
    csv_path = root.parent / "input" / "virtual_screening.csv"
    try:
        if csv_path.exists():
            with csv_path.open(newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    peptide = str(row.get("peptide_description") or "").strip()
                    if peptide:
                        return peptide
    except Exception:
        pass
    for path in pose_files or []:
        try:
            rel_parts = path.relative_to(root).parts
        except Exception:
            rel_parts = path.parts
        if len(rel_parts) >= 2:
            parent = rel_parts[-2]
            if parent and parent.lower() not in {"output", "outputs", "results"}:
                return parent
    return root.name


def _cleanup_rapidock_pose_state(session):
    from .named_selection import list_groups, remove_group

    prior_models = [model for model in getattr(session, "_codex_rapidock_pose_models", []) or [] if model is not None]
    if prior_models:
        try:
            session.models.close(prior_models)
        except Exception as err:
            session.logger.warning(f"Could not close prior RAPiDock pose models: {err}")
    session._codex_rapidock_pose_models = []

    for name in list(list_groups(session)):
        if str(name).startswith("rapidock_"):
            try:
                remove_group(session, name)
            except Exception as err:
                session.logger.warning(f"Could not remove prior RAPiDock group '{name}': {err}")


def _open_hpepdock_package_split_on_ui(session, output_dir, hpe_package, peptide_token, *, limit=5):
    """Open one receptor model plus separate peptide pose models for Models-panel clarity."""
    from .named_selection import add_group

    receptor_path = hpe_package.get("receptor")
    summaries = list(hpe_package.get("summaries") or [])
    if not summaries:
        return None

    ranked = sorted(summaries, key=lambda row: (_hpepdock_summary_rank(row), _rapidock_pose_sort_key(row.get("pose_pdb") or "")))
    ranked = ranked[: max(1, int(limit or 5))]
    if not ranked:
        return None

    opened_models = []
    opened_paths = []
    receptor_models = []

    if receptor_path and Path(receptor_path).exists():
        before = _model_identity_set(session)
        _run_chimerax(session, "open " + _quote_command_token(str(receptor_path)))
        receptor_models = [
            model for model in _new_models_since(session, before)
            if hasattr(model, "atoms") and len(getattr(model, "atoms", [])) > 0
        ]
        if len(receptor_models) > 1:
            display_receptor = receptor_models[0]
            try:
                session.models.close(receptor_models[1:])
            except Exception as err:
                session.logger.warning(f"Could not close extra HPEPDOCK receptor models: {err}")
            receptor_models = [display_receptor]
        for model in receptor_models:
            try:
                model.name = f"HPEPDOCK receptor - {Path(output_dir).name}"
            except Exception:
                pass
            spec = f"#{getattr(model, 'id_string', '?')}"
            for command in (
                f"color {spec} lightgray",
                f"cartoon {spec}",
                f"hide {spec} atoms",
            ):
                try:
                    _run_chimerax(session, command)
                except Exception:
                    pass
            try:
                add_group(session, f"rapidock_{peptide_token}_receptor", spec, color="#d0d0d0")
            except Exception as err:
                session.logger.warning(f"Could not create HPEPDOCK receptor group: {err}")
        opened_models.extend(receptor_models)
        if receptor_models:
            opened_paths.append(Path(receptor_path))

    peptide_models = []
    for display_index, item in enumerate(ranked, start=1):
        pose_path = str(item.get("minimized_peptide_pdb") or "").strip()
        if not pose_path or not Path(pose_path).exists():
            pose_path = str(item.get("pose_pdb") or "").strip()
        if not pose_path or not Path(pose_path).exists():
            continue

        before = _model_identity_set(session)
        _run_chimerax(session, "open " + _quote_command_token(pose_path))
        new_models = [
            model for model in _new_models_since(session, before)
            if hasattr(model, "atoms") and len(getattr(model, "atoms", [])) > 0
        ]
        hpe_rank = _hpepdock_pose_rank_label(item)
        validation_rank = item.get("validation_rank") or display_index
        status = str(item.get("validation_status") or "pose")
        color = _RAPIDOCK_POSE_COLORS[(display_index - 1) % len(_RAPIDOCK_POSE_COLORS)]
        group_name = f"rapidock_{peptide_token}_v{display_index}_hpe{hpe_rank}"
        for model in new_models:
            try:
                model.name = f"HPEPDOCK v{display_index} peptide - HPE rank {hpe_rank} ({status})"
            except Exception:
                pass
            spec = f"#{getattr(model, 'id_string', '?')}"
            for command in (
                f"show {spec} atoms",
                f"style {spec} stick",
                f"color {spec} {color}",
            ):
                try:
                    _run_chimerax(session, command)
                except Exception:
                    pass
            try:
                add_group(session, group_name, spec, color=_RAPIDOCK_POSE_COLOR_HEX.get(color))
            except Exception as err:
                session.logger.warning(f"Could not create HPEPDOCK peptide group '{group_name}': {err}")
        peptide_models.extend(new_models)
        opened_models.extend(new_models)
        opened_paths.append(Path(pose_path))

    if not peptide_models:
        return None

    best = next((item for item in summaries if item.get("recommended") == "yes"), None)
    if best is None:
        best = ranked[0]
    session._codex_rapidock_pose_models = opened_models
    return "\n".join([
        f"Opened HPEPDOCK as separate Models-panel entries: {len(receptor_models)} receptor + {len(peptide_models)} peptide pose model(s).",
        "Loaded into the current 3D scene without changing the camera.",
        "Complex PDBs, minimization outputs, and validation tables were still written for export/publication.",
        "Opened files:",
        *[f"- {path}" for path in opened_paths],
        "HPEPDOCK validation package:",
        f"- summary: {hpe_package.get('summary_md') or hpe_package.get('summary_tsv')}",
        f"- validation table: {hpe_package.get('validation_tsv')}",
        f"- recommended table: {hpe_package.get('recommended_tsv')}",
        f"- best report: {hpe_package.get('best_report_md')}",
        (
            f"- recommended pose: validation rank {best.get('validation_rank')}, "
            f"HPE rank {best.get('rank')}, {best.get('validation_status')} "
            f"({best.get('validation_flags')})"
        ),
    ])


def _open_rapidock_pose_outputs_on_ui(session, output_dir, peptide=None, *, limit=5):
    from .named_selection import add_group

    output_dir = Path(output_dir).expanduser()
    hpe_package = {}
    try:
        from .hpepdock_client import prepare_hpepdock_results

        hpe_package = prepare_hpepdock_results(output_dir, top_n=limit)
        summary_tsv = hpe_package.get("summary_tsv")
        complexes = hpe_package.get("complexes") or []
        best = next((item for item in hpe_package.get("summaries", []) if item.get("recommended") == "yes"), None)
        if summary_tsv:
            try:
                detail = f"HPEPDOCK package ready: {len(complexes)} complex PDB(s), summary {summary_tsv}"
                if best:
                    detail += (
                        f"; recommended HPE rank {best.get('rank')} "
                        f"({best.get('validation_status')}, refined rank {best.get('validation_rank')})"
                    )
                session.logger.info(detail)
            except Exception:
                pass
    except Exception as err:
        try:
            session.logger.warning(f"HPEPDOCK result packaging skipped: {err}")
        except Exception:
            pass
    if not hpe_package.get("summaries"):
        hpe_package = _hpepdock_existing_package_from_files(output_dir, limit=limit)
        if hpe_package.get("summaries"):
            try:
                session.logger.info("HPEPDOCK package loaded from existing summary/peptide PDB files.")
            except Exception:
                pass
    _cleanup_rapidock_pose_state(session)
    peptide_token = _sanitize_rapidock_group_token(peptide or _infer_rapidock_peptide(output_dir))
    if hpe_package.get("summaries"):
        split_message = _open_hpepdock_package_split_on_ui(
            session,
            output_dir,
            hpe_package,
            peptide_token,
            limit=limit,
        )
        if split_message:
            return split_message

    pose_files = _rapidock_output_files(output_dir, limit=limit)
    if not pose_files:
        return f"RAPiDock produced no output files in {output_dir}. Inspect the logs."

    peptide_token = _sanitize_rapidock_group_token(peptide or _infer_rapidock_peptide(output_dir, pose_files))
    opened_models = []
    opened_paths = []
    for index, path in enumerate(pose_files, start=1):
        before = _model_identity_set(session)
        _run_chimerax(session, "open " + _quote_command_token(str(path)))
        new_models = [
            model for model in _new_models_since(session, before)
            if hasattr(model, "atoms") and len(getattr(model, "atoms", [])) > 0
        ]
        color = _RAPIDOCK_POSE_COLORS[(index - 1) % len(_RAPIDOCK_POSE_COLORS)]
        group_name = f"rapidock_{peptide_token}_pose{index}"
        for model in new_models:
            try:
                model.name = group_name
            except Exception:
                pass
            spec = f"#{getattr(model, 'id_string', '?')}"
            for command in (
                f"color {spec} bychain",
                f"style {spec} stick",
                f"cartoon {spec}",
                f"color {spec} {color}",
            ):
                try:
                    _run_chimerax(session, command)
                except Exception:
                    pass
            try:
                add_group(session, group_name, spec, color=_RAPIDOCK_POSE_COLOR_HEX.get(color))
            except Exception as err:
                session.logger.warning(f"Could not create RAPiDock group '{group_name}': {err}")
        opened_models.extend(new_models)
        opened_paths.append(path)

    session._codex_rapidock_pose_models = opened_models
    extra_lines = []
    if hpe_package.get("summaries"):
        best = next((item for item in hpe_package.get("summaries", []) if item.get("recommended") == "yes"), None)
        if best is None:
            best = sorted(
                hpe_package.get("summaries", []),
                key=lambda row: int(float(row.get("validation_rank") or 999999)),
            )[0]
        extra_lines.extend([
            "HPEPDOCK validation package:",
            f"- summary: {hpe_package.get('summary_md') or hpe_package.get('summary_tsv')}",
            f"- validation table: {hpe_package.get('validation_tsv')}",
            f"- recommended table: {hpe_package.get('recommended_tsv')}",
            f"- best report: {hpe_package.get('best_report_md')}",
            (
                f"- recommended pose: validation rank {best.get('validation_rank')}, "
                f"HPE rank {best.get('rank')}, {best.get('validation_status')} "
                f"({best.get('validation_flags')})"
            ),
        ])
    return "\n".join(
        [
            f"Opened {len(opened_models)} {'HPEPDOCK complex' if hpe_package.get('summaries') else 'RAPiDock pose'} model(s) from {output_dir}.",
            "Loaded into the current 3D scene without changing the camera.",
            "Opened files:",
            *[f"- {path}" for path in opened_paths],
            *extra_lines,
        ]
    )


def load_rapidock_outputs(session, output_dir, peptide=None, *, limit=5):
    if _is_qt_main_thread():
        return _open_rapidock_pose_outputs_on_ui(
            session,
            output_dir,
            peptide=peptide,
            limit=limit,
        )

    result_box = {}
    event = threading.Event()

    def finish():
        try:
            result_box["message"] = _open_rapidock_pose_outputs_on_ui(
                session,
                output_dir,
                peptide=peptide,
                limit=limit,
            )
        except Exception as err:
            result_box["error"] = err
        finally:
            event.set()

    try:
        session.ui.thread_safe(finish)
    except Exception as err:
        # Critic P0: don't fall back to running finish() on the worker thread —
        # mutates session.models off-thread, crashes macOS OpenGL.
        try:
            session.logger.warning(
                f"RAPiDock auto-load aborted: cannot reach UI thread ({err}). "
                f"Use /rapidock_load {output_dir} to import manually."
            )
        except Exception:
            pass
        return ""
    if not event.wait(180):
        try:
            session.logger.warning(
                f"RAPiDock auto-load UI bounce timed out after 180s; "
                f"use /rapidock_load {output_dir} to import manually."
            )
        except Exception:
            pass
        return ""
    if "error" in result_box:
        raise result_box["error"]
    return result_box.get("message", "")


def _cancel_rapidock_output_watchers(session):
    watchers = getattr(session, "_codex_rapidock_watchers", None)
    if not watchers:
        try:
            session._codex_rapidock_watchers = {}
        except Exception:
            pass
        return
    try:
        items = list(watchers.values())
    except Exception:
        items = []
    for watcher in items:
        stop_event = watcher.get("stop") if isinstance(watcher, dict) else None
        if stop_event is not None:
            try:
                stop_event.set()
            except Exception:
                pass
    try:
        session._codex_rapidock_watchers = {}
    except Exception:
        pass


def _rapidock_begin_load(load_state):
    if load_state is None:
        return True
    lock = load_state.get("lock")
    if lock is None:
        if load_state.get("loaded") or load_state.get("loading"):
            return False
        load_state["loading"] = True
        return True
    with lock:
        if load_state.get("loaded") or load_state.get("loading"):
            return False
        load_state["loading"] = True
        return True


def _rapidock_finish_load(load_state, loaded):
    if load_state is None:
        return
    lock = load_state.get("lock")
    if lock is None:
        if loaded:
            load_state["loaded"] = True
        load_state["loading"] = False
        return
    with lock:
        if loaded:
            load_state["loaded"] = True
        load_state["loading"] = False


def _rapidock_is_loaded(load_state):
    if load_state is None:
        return False
    lock = load_state.get("lock")
    if lock is None:
        return bool(load_state.get("loaded"))
    with lock:
        return bool(load_state.get("loaded"))


def _rapidock_load_once(session, output_dir, peptide=None, load_state=None):
    if not _rapidock_begin_load(load_state):
        return None
    loaded = False
    try:
        message = load_rapidock_outputs(session, output_dir, peptide=peptide)
        loaded = bool(getattr(session, "_codex_rapidock_pose_models", None))
        return message
    finally:
        _rapidock_finish_load(load_state, loaded)


def _start_rapidock_output_watcher(session, output_dir, peptide=None, load_state=None, *,
                                   timeout_seconds=3600, poll_seconds=5, require_done=False):
    output_dir = Path(output_dir).expanduser()
    key = str(output_dir)
    watchers = getattr(session, "_codex_rapidock_watchers", None)
    if watchers is None:
        watchers = {}
        session._codex_rapidock_watchers = watchers
    existing = watchers.get(key)
    if existing is not None:
        stop_event = existing.get("stop")
        if stop_event is not None:
            try:
                stop_event.set()
            except Exception:
                pass

    stop_event = threading.Event()

    def watcher():
        import time
        # Critic P6: prefer an explicit done.flag sentinel (written by Docker
        # container after inference completes) so we never read truncated PDBs.

        deadline = time.time() + timeout_seconds
        last_count = 0
        stable_polls = 0
        while not stop_event.is_set():
            if _rapidock_is_loaded(load_state):
                break
            done_flags = list(output_dir.rglob(".codex_rapidock_done"))
            pose_files = _rapidock_output_files(output_dir, limit=999999)
            count = len(pose_files)
            if require_done:
                ready = bool(done_flags and count > 0)
                if ready:
                    stable_polls = 999
            elif done_flags and count > 0:
                ready = True
                stable_polls = 999  # short-circuit
            else:
                ready = count > 0 and count == last_count
                if count > 0 and count == last_count:
                    stable_polls += 1
                else:
                    stable_polls = 0
            last_count = count
            if ready and (done_flags or stable_polls >= 1):
                if stop_event.is_set() or _rapidock_is_loaded(load_state):
                    break
                try:
                    message = _rapidock_load_once(session, output_dir, peptide=peptide, load_state=load_state)
                    if message:
                        session.logger.info(message)
                except Exception as err:
                    session.logger.warning(f"RAPiDock manual run watcher could not load poses from {output_dir}: {err}")
                    break
                if _rapidock_is_loaded(load_state):
                    break
            if time.time() >= deadline:
                session.logger.warning(
                    f"RAPiDock manual run watcher timed out at {output_dir}; use /rapidock_load to load manually."
                )
                break
            stop_event.wait(poll_seconds)
        try:
            current = getattr(session, "_codex_rapidock_watchers", {})
            if current.get(key, {}).get("stop") is stop_event:
                current.pop(key, None)
        except Exception:
            pass

    thread = threading.Thread(target=watcher, daemon=True)
    watchers[key] = {"thread": thread, "stop": stop_event}
    thread.start()
    return thread


RAPIDOCK_ENGINES = ("auto", "native", "docker", "hpepdock")


def _resolve_rapidock_engine(session, requested):
    """Pick the actual engine to use.

    Decision matrix:
      'native'   → run via local venv (Linux only); refuse on macOS.
      'docker'   → run via Docker (linux/amd64); requires Docker daemon.
      'hpepdock' → submit to HPEPDOCK 2.0 web service; works anywhere.
      'auto'     → Mac=hpepdock (per critic recommendation), Linux=native.
    """
    import platform as _platform
    requested = str(requested or "auto").strip().lower()
    if requested not in RAPIDOCK_ENGINES:
        try:
            session.logger.warning(
                f"Unknown RAPiDock engine '{requested}' — falling back to 'auto'."
            )
        except Exception:
            pass
        requested = "auto"
    if requested != "auto":
        return requested
    if _platform.system() == "Darwin" and not os.environ.get("RAPIDOCK_ALLOW_EMULATION"):
        return "hpepdock"
    return "native"


def launch_rapidock_prediction(
    session,
    peptide=None,
    *,
    model_hint=None,
    mode="global",
    save_root=None,
    n_samples=5,
    pocket_buffer=12.0,
    use_pocket_crop=True,
    pocket_index=1,
    engine="auto",
    email=None,
):
    import platform as _platform

    entries = _rapidock_sequence_entries(session, model_hint=model_hint)
    if not entries:
        return "No protein chains are open for RAPiDock."
    peptide = str(peptide or "").strip().upper()
    if not peptide:
        return "RAPiDock needs a peptide sequence. Use /rapidock <peptide sequence>."

    # critic P7: peptide length validation (HPEPDOCK 3-25, RAPiDock benchmarked ≤15)
    invalid_aa = sorted(set(peptide) - set("ACDEFGHIKLMNPQRSTVWY"))
    if invalid_aa:
        return f"RAPiDock peptide contains non-canonical amino acids: {invalid_aa}"
    if len(peptide) < 3:
        return f"RAPiDock peptide too short ({len(peptide)} aa); need at least 3."
    if len(peptide) > 25:
        return (
            f"RAPiDock peptide too long ({len(peptide)} aa); HPEPDOCK supports ≤25 and "
            f"RAPiDock benchmarks max ≤15 — split into shorter epitopes."
        )
    length_warn = None
    if len(peptide) > 15:
        length_warn = (
            f"Peptide is {len(peptide)} aa — RAPiDock/HPEPDOCK accuracy degrades above ~15 aa; treat poses as exploratory."
        )
        try:
            session.logger.warning(length_warn)
        except Exception:
            pass

    resolved_engine = _resolve_rapidock_engine(session, engine)

    _cancel_rapidock_output_watchers(session)
    _cleanup_rapidock_pose_state(session)
    repo_root = _rapidock_repo_root()
    import time

    desktop_default = Path.home() / "Desktop" / "RAPiDock"
    if save_root is None:
        chosen = _prompt_rapidock_save_location(session, desktop_default)
        if chosen is None:
            return "RAPiDock cancelled: no save folder was chosen."
        run_root = chosen
    else:
        run_root = Path(save_root).expanduser()
    run_root.mkdir(parents=True, exist_ok=True)
    run_dir = run_root / time.strftime("codex_rapidock_%Y%m%d_%H%M%S")
    input_dir = run_dir / "input"
    output_dir = run_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = input_dir / "virtual_screening.csv"

    cropped_pdb_path = None
    crop_descriptor = None
    crop_notes = []
    if use_pocket_crop:
        try:
            from .semantic import find_kvfinder_pockets

            # KVFinder + ChimeraX `save` both touch OpenGL/Models, must run on
            # the main thread (we're typically inside a worker thread here).
            crop_box = {}

            def _do_kvfinder_and_crop():
                try:
                    # Fetch enough pockets to honor user's pocket_index choice.
                    fetch_n = max(int(pocket_index), 1)
                    pockets = find_kvfinder_pockets(
                        session,
                        model_hint=model_hint,
                        top_n=fetch_n,
                        lining_shell=3.5,
                        min_volume=30.0,
                        min_depth=1.0,
                    )
                    if not pockets:
                        crop_box["pockets"] = []
                        return
                    crop_box["pockets"] = pockets
                    # Clamp to available; 1-based index from user → 0-based list
                    chosen_idx = max(1, min(int(pocket_index), len(pockets))) - 1
                    chosen_pocket = pockets[chosen_idx]
                    pdb_path, kept_count, descriptor = _crop_receptor_around_pocket(
                        session,
                        chosen_pocket,
                        input_dir / "receptor_cropped.pdb",
                        buffer=pocket_buffer,
                    )
                    crop_box["pdb"] = pdb_path
                    crop_box["count"] = kept_count
                    crop_box["descriptor"] = descriptor
                    crop_box["chosen_index"] = chosen_idx + 1
                    crop_box["available"] = len(pockets)
                except Exception as err:
                    crop_box["error"] = err

            if _is_qt_main_thread():
                _do_kvfinder_and_crop()
            else:
                event = threading.Event()

                def runner():
                    try:
                        _do_kvfinder_and_crop()
                    finally:
                        event.set()

                try:
                    session.ui.thread_safe(runner)
                except Exception as err:
                    crop_box["error"] = err
                    event.set()
                # KVFinder + crop can take a while on large structures; cap
                # at 5 min so a hung pipeline surfaces clearly rather than
                # leaking the worker thread.
                if not event.wait(300):
                    crop_box["error"] = TimeoutError(
                        "KVFinder + pocket crop did not complete within 300s"
                    )

            if "error" in crop_box:
                crop_notes.append(f"Pocket crop step failed ({crop_box['error']}); using full receptor sequence.")
            elif not crop_box.get("pockets"):
                crop_notes.append("KVFinder found no eligible pocket — falling back to full receptor sequence.")
            else:
                cropped_pdb_path = crop_box.get("pdb")
                kept_count = crop_box.get("count", 0)
                descriptor = crop_box.get("descriptor")
                if cropped_pdb_path is not None:
                    crop_descriptor = descriptor
                    tag_text = ", ".join((descriptor or {}).get("pocket_tags") or []) or "no motif evidence"
                    chosen = crop_box.get("chosen_index", 1)
                    available = crop_box.get("available", 1)
                    crop_notes.append(
                        f"Pocket-cropped receptor: kept {kept_count} residues "
                        f"in {pocket_buffer:.0f} Å cube around KVFinder pocket #{chosen} of {available} "
                        f"(vol={(descriptor or {}).get('pocket_volume', 0):.0f} Å³, tags: {tag_text}). "
                        f"To dock against a different pocket pass pocket_index=N to the launcher."
                    )
                else:
                    crop_notes.append(
                        f"Pocket cropping skipped: {descriptor}. Falling back to full receptor sequence."
                    )
        except Exception as err:
            crop_notes.append(f"Pocket crop step failed ({err}); using full receptor sequence.")

    if cropped_pdb_path is not None:
        protein_description = str(cropped_pdb_path)
    else:
        protein_description = entries[0]["sequence"]

    _write_rapidock_virtual_screening_csv(entries, peptide, csv_path, protein_description=protein_description)
    rapidock_load_state = {"loaded": False, "lock": threading.Lock()}

    if resolved_engine == "hpepdock":
        if cropped_pdb_path is None:
            return (
                "HPEPDOCK needs an actual receptor PDB file. KVFinder pocket cropping "
                "did not produce one — check that an atomic structure is open."
            )
        # Email is optional — HPEPDOCK only uses it for notifications, and we
        # poll the results page directly. Skip the prompt entirely; the client
        # falls back to a placeholder if the user hasn't set one.
        chosen_email = (
            (email or "").strip()
            or _hpepdock_email_from_session(session)
            or os.environ.get("HPEPDOCK_EMAIL", "").strip()
            or "noreply@chimerax-codex-bridge.local"
        )
        session._codex_hpepdock_email = chosen_email

        # Build HPEPDOCK site-mode residue list from the KVFinder pocket.
        # HPEPDOCK 2.0 expects "<resnum>:<chain>" tokens per its help page,
        # e.g. "195:A, 203-206:A, 108:B" — note resnum FIRST, then colon, chain.
        site_residue_spec = ""
        pocket_obj = (crop_box or {}).get("pockets", [None])[0] if isinstance(crop_box, dict) else None
        if pocket_obj is not None:
            lining = pocket_obj.get("lining_specs") or []
            by_chain = {}
            for spec in lining:
                # spec like '#1/A:1122'
                tail = spec.split("/", 1)[-1] if "/" in spec else spec
                tail = tail.lstrip("/")
                if ":" not in tail:
                    continue
                chain_part, _, num_part = tail.partition(":")
                try:
                    by_chain.setdefault(chain_part, set()).add(int(num_part))
                except ValueError:
                    continue
            site_tokens = []
            total_residues = 0
            for chain_id in sorted(by_chain.keys()):
                numbers = sorted(by_chain[chain_id])
                # Compress consecutive runs into "start-end:chain" tokens.
                run_start = numbers[0]
                prev = numbers[0]
                for n in numbers[1:] + [None]:
                    if n is not None and n == prev + 1:
                        prev = n
                        continue
                    if run_start == prev:
                        site_tokens.append(f"{run_start}:{chain_id}")
                    else:
                        site_tokens.append(f"{run_start}-{prev}:{chain_id}")
                    if n is not None:
                        run_start = n
                        prev = n
                total_residues += len(numbers)
            if site_tokens:
                # HPEPDOCK sitenum1 input is a 33-char text field — keep payload short.
                packed = ",".join(site_tokens)
                if len(packed) > 220:
                    # Take the first few range tokens to fit comfortably.
                    keep = []
                    running = 0
                    for tok in site_tokens:
                        if running + len(tok) + 1 > 220:
                            break
                        keep.append(tok)
                        running += len(tok) + 1
                    packed = ",".join(keep)
                site_residue_spec = packed
                crop_notes.append(
                    f"HPEPDOCK site mode: focusing on {total_residues} pocket residue(s) → '{packed}'"
                )

        _start_rapidock_output_watcher(
            session,
            output_dir,
            peptide=peptide,
            load_state=rapidock_load_state,
            require_done=True,
        )

        def _run_hpepdock():
            try:
                from .hpepdock_client import run_hpepdock_pipeline

                result = run_hpepdock_pipeline(
                    session,
                    cropped_pdb_path,
                    peptide,
                    chosen_email,
                    output_dir / "codex_rapidock_case",
                    top_n=int(n_samples),
                    jobname=f"ChimeraX-{Path(cropped_pdb_path).stem}-{peptide[:8]}",
                    site_residues=site_residue_spec or None,
                )
            except Exception as err:
                try:
                    session.logger.error(f"HPEPDOCK run failed: {err}")
                except Exception:
                    pass
                return
            try:
                session.logger.info(
                    f"HPEPDOCK job {result['job_id']} complete — {len(result['poses'])} poses written. "
                    f"Status page: {result['status_url']}"
                )
            except Exception:
                pass

        threading.Thread(target=_run_hpepdock, daemon=True).start()
        summary = [
            f"Started HPEPDOCK 2.0 (web service) for peptide {peptide} against pocket-cropped receptor.",
            "Engine: hpepdock — runs on HuangLab servers, no local GPU/Docker needed.",
            "Note: HPEPDOCK uses a hierarchical algorithm, not RAPiDock's diffusion model. "
            "Treat poses as a different model's prediction.",
            f"Result email recipient: {chosen_email}",
        ]
        if length_warn:
            summary.append(length_warn)
        for note in crop_notes:
            summary.append(note)
        if cropped_pdb_path is not None:
            summary.append(f"Receptor uploaded: {cropped_pdb_path}")
        summary.extend([
            "Output folder: " + str(output_dir),
            "Typical wall time: 5–15 minutes (queue dependent).",
        ])
        return "\n".join(summary)

    if resolved_engine == "docker":
        try:
            from .docker_runner import (
                _build_rapidock_docker_image,
                _docker_available,
                _run_rapidock_in_docker,
                monitor_rapidock_docker,
            )
        except Exception as err:
            return f"RAPiDock Docker runner could not be imported: {err}"
        if not _docker_available():
            return (
                "RAPiDock Docker engine selected but Docker daemon is not reachable.\n"
                "Start Docker Desktop (or `dockerd` on Linux) and retry, or use engine='hpepdock'."
            )
        mem_warn = _check_docker_memory(session, min_gib=12)
        if mem_warn:
            try:
                session.logger.warning(mem_warn)
            except Exception:
                pass
        if repo_root is None:
            return "Docker engine needs a local RAPiDock checkout at ~/RAPiDock or $RAPIDOCK_REPO. Run /rapidock_setup first."
        if _platform.system() == "Darwin" and not os.environ.get("RAPIDOCK_ALLOW_EMULATION"):
            return (
                "Docker engine on macOS requires linux/amd64 emulation (~2-6 hours per peptide).\n"
                "Set RAPIDOCK_ALLOW_EMULATION=1 to enable, or use engine='hpepdock' for the web fallback."
            )

        _start_rapidock_output_watcher(session, output_dir, peptide=peptide, load_state=rapidock_load_state)

        def _docker_build_then_run():
            ok, build_message = _build_rapidock_docker_image(session, repo_root)
            if not ok:
                try:
                    session.logger.error("RAPiDock Docker image build failed: " + build_message)
                except Exception:
                    pass
                return
            try:
                process, _command_text = _run_rapidock_in_docker(
                    session,
                    csv_path,
                    output_dir,
                    repo_root,
                    n_samples,
                    mode=mode,
                    receptor_pdb=cropped_pdb_path,
                    peptide=peptide,
                )
            except Exception as err:
                try:
                    session.logger.error(f"RAPiDock Docker launch failed: {err}")
                except Exception:
                    pass
                return

            def _load_success():
                message = _rapidock_load_once(session, output_dir, peptide=peptide, load_state=rapidock_load_state)
                if message:
                    try:
                        session.logger.info(message)
                    except Exception:
                        pass

            monitor_rapidock_docker(session, process, output_dir, _load_success)

        threading.Thread(target=_docker_build_then_run, daemon=True).start()
        summary = [
            f"Started RAPiDock Docker ({mode}, linux/amd64 CPU) for peptide {peptide} against {entries[0]['spec']} (N={int(n_samples)}).",
        ]
        for note in crop_notes:
            summary.append(note)
        if cropped_pdb_path is not None:
            summary.append(f"Receptor sent: {cropped_pdb_path}")
        summary.extend([
            "Input CSV: " + str(csv_path),
            "Output folder: " + str(output_dir),
            "Docker image build/run is continuing in the background.",
        ])
        return "\n".join(summary)

    if repo_root is None:
        _start_rapidock_output_watcher(session, output_dir, peptide=peptide, load_state=rapidock_load_state)

        def _auto_setup_then_relaunch():
            try:
                message = setup_rapidock_repo(
                    None, session=session, skip_models=False, gpu="auto",
                )
            except Exception as err:
                try:
                    session.logger.error(f"RAPiDock auto-setup crashed: {err}")
                except Exception:
                    pass
                return
            try:
                session.logger.info(message)
            except Exception:
                pass
            try:
                _setup_log(session, "Setup finished — re-launching RAPiDock for your peptide …")
            except Exception:
                pass

            def _relaunch_on_ui():
                try:
                    launch_rapidock_prediction(
                        session,
                        peptide=peptide,
                        model_hint=model_hint,
                        mode=mode,
                        save_root=run_root,
                        n_samples=n_samples,
                        pocket_buffer=pocket_buffer,
                        use_pocket_crop=use_pocket_crop,
                    )
                except Exception as err:
                    try:
                        session.logger.error(f"RAPiDock auto-relaunch failed: {err}")
                    except Exception:
                        pass

            try:
                session.ui.thread_safe(_relaunch_on_ui)
            except Exception:
                _relaunch_on_ui()

        threading.Thread(target=_auto_setup_then_relaunch, daemon=True).start()

        lines = [
            "RAPiDock repo not found locally — auto-setup started in background.",
            "Steps queued: git clone → venv → pip install (torch + e3nn + esm + rdkit + PyG) → download 2× model checkpoints (~110 MB).",
            "Typical duration: 5–15 minutes (depends on bandwidth + CPU). Watch this log for progress.",
            "When setup finishes, your RAPiDock prediction will re-launch automatically.",
        ]
        for note in crop_notes:
            lines.append(note)
        if cropped_pdb_path is not None:
            lines.append(f"Cropped receptor saved at: {cropped_pdb_path}")
        lines.extend([
            "Inputs prepared at:",
            f"  {csv_path}",
            "Watcher active at:",
            f"  {output_dir}",
            f"Generation count (--N): {int(n_samples)}.",
        ])
        return "\n".join(lines)

    rapidock_python = _rapidock_python_executable(repo_root)
    has_pyrosetta = _rapidock_pyrosetta_available(rapidock_python)
    command = [
        rapidock_python,
        "inference.py",
        "--protein_peptide_csv",
        str(csv_path),
        "--output_dir",
        str(output_dir),
        "--N",
        str(int(n_samples)),
        "--model_dir",
        "train_models/CGTensorProductEquivariantModel",
        "--ckpt",
        f"rapidock_{mode}.pt",
        "--batch_size",
        "4",
        "--no_final_step_noise",
        "--inference_steps",
        "16",
        "--actual_steps",
        "16",
        "--conformation_partial",
        "1:1:1",
        "--cpu",
        "10",
    ]
    if has_pyrosetta:
        command.extend(["--scoring_function", "ref2015"])
    else:
        try:
            session.logger.info(
                "RAPiDock: pyrosetta not detected in the RAPiDock venv — running without ref2015 relax (raw pose ranking only)."
            )
        except Exception:
            pass
    command_text = " ".join(_quote_command_token(part) for part in command)
    process = subprocess.Popen(
        command,
        cwd=str(repo_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_clean_subprocess_env(),
    )
    _start_rapidock_output_watcher(session, output_dir, peptide=peptide, load_state=rapidock_load_state)

    def monitor():
        stdout, stderr = process.communicate()
        stdout_text = stdout.decode("utf-8", errors="replace") if isinstance(stdout, bytes) else str(stdout or "")
        stderr_text = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else str(stderr or "")

        def finish():
            if process.returncode != 0:
                detail = (stderr_text or stdout_text or "").strip()
                if len(detail) > 3000:
                    detail = detail[-3000:]
                session.logger.error(
                    "RAPiDock prediction failed.\n"
                    f"Command: {command_text}\n"
                    f"Exit code: {process.returncode}\n"
                    f"{detail}"
                )
                return
            if stdout_text.strip():
                session.logger.info(stdout_text.strip()[-3000:])
            message = _rapidock_load_once(session, output_dir, peptide=peptide, load_state=rapidock_load_state)
            if message:
                session.logger.info(message)

        try:
            session.ui.thread_safe(finish)
        except Exception:
            finish()

    threading.Thread(target=monitor, daemon=True).start()
    summary = [
        f"Started RAPiDock ({mode}) for peptide {peptide} against {entries[0]['spec']} (N={int(n_samples)}).",
    ]
    for note in crop_notes:
        summary.append(note)
    if cropped_pdb_path is not None:
        summary.append(f"Receptor sent: {cropped_pdb_path}")
    summary.extend([
        "Input CSV: " + str(csv_path),
        "Output folder: " + str(output_dir),
        "Command: " + command_text,
    ])
    return "\n".join(summary)


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


def _register_foldseek_hit_alignments(session, results, opened_models, set_name):
    """Stash a per-hit pairwise payload in `session._codex_bridge_hit_alignments`.

    Foldseek produces a single multi-MSA (query + N hits). The sequence-bar
    needs a per-hit pairwise (query, hit) payload so picking each hit chain
    from the dropdown surfaces that hit's alignment to the query.
    """
    try:
        alignment_array = results.sequence_alignment_array()
    except Exception:
        return
    if alignment_array is None or len(alignment_array) < 2:
        return

    query_chain = getattr(results, "query_chain", None)
    if query_chain is None:
        return
    query_struct = getattr(query_chain, "structure", None)
    if query_struct is None:
        return
    query_spec = f"#{query_struct.id_string}/{getattr(query_chain, 'chain_id', '?')}"
    query_name = f"{getattr(query_struct, 'name', 'query')}_{getattr(query_chain, 'chain_id', '?')}"
    query_aligned = _bytes_row_to_sequence(alignment_array[0])

    hits = list(getattr(results, "hits", []) or [])
    registry = getattr(session, "_codex_bridge_hit_alignments", None)
    if registry is None:
        registry = {}
        session._codex_bridge_hit_alignments = registry

    last_payload = None
    for hit_index, model in enumerate(opened_models):
        row_index = hit_index + 1
        if row_index >= len(alignment_array):
            break
        hit_aligned = _bytes_row_to_sequence(alignment_array[row_index])
        hit_chain = next(iter(getattr(model, "chains", []) or []), None)
        if hit_chain is None:
            continue
        chain_id = getattr(hit_chain, "chain_id", "?")
        hit_spec = f"#{model.id_string}/{chain_id}"
        hit = hits[hit_index] if hit_index < len(hits) else {}
        hit_label = str(hit.get("database_full_id") or hit.get("database_id") or model.name)
        identity_value = hit.get("pident")
        try:
            identity = float(identity_value) / 100.0 if identity_value is not None else _aligned_identity_pair(query_aligned, hit_aligned)
        except Exception:
            identity = _aligned_identity_pair(query_aligned, hit_aligned)
        payload = {
            "alignment_id": f"foldseek_{set_name}_{model.id_string}",
            "name": f"Foldseek pair {hit_label} vs {query_name}",
            "reference": {
                "spec": query_spec,
                "display": f"{query_spec} · {getattr(query_struct, 'name', 'query')}",
                "sequence": query_aligned.replace("-", ""),
                "aligned": query_aligned,
                "column_map": _column_map_from_chain_for_aligned(query_chain, query_aligned),
            },
            "moving": {
                "spec": hit_spec,
                "display": f"{hit_spec} · {hit_label}",
                "sequence": hit_aligned.replace("-", ""),
                "aligned": hit_aligned,
                "column_map": _column_map_from_chain_for_aligned(hit_chain, hit_aligned),
            },
            "length": len(query_aligned),
            "identity": identity,
        }
        for key in (query_spec, hit_spec, f"#{model.id_string}", f"#{query_struct.id_string}"):
            if key:
                registry[key] = payload
        last_payload = payload

    if last_payload is not None:
        session._codex_bridge_last_structure_alignment_payload = last_payload
        session._codex_bridge_last_alignment_id = last_payload.get("alignment_id")
        try:
            _publish_sequence_alignment_refresh(session)
        except Exception:
            pass


def _aligned_identity_pair(a, b):
    matches = 0
    aligned = 0
    for ca, cb in zip(a, b):
        if ca == "-" or cb == "-":
            continue
        aligned += 1
        if ca == cb:
            matches += 1
    return (matches / aligned) if aligned else 0.0


def _column_map_from_chain_for_aligned(chain, aligned_text):
    """Same logic as sequence_bar._column_map_from_chain but local for this module."""
    structure = getattr(chain, "structure", None)
    model_prefix = (
        f"#{structure.id_string}" if structure is not None and getattr(structure, "id_string", None) else ""
    )
    residues = list(getattr(chain, "existing_residues", []) or [])
    column_map = []
    residue_index = 0
    for ch in str(aligned_text or ""):
        if ch == "-":
            column_map.append(None)
            continue
        is_insertion = ch.islower()
        spec = ""
        if residue_index < len(residues):
            residue = residues[residue_index]
            raw_spec = getattr(residue, "atomspec", "") or ""
            if raw_spec.startswith("#"):
                spec = raw_spec
            elif raw_spec:
                spec = f"{model_prefix}{raw_spec}" if model_prefix else raw_spec
            residue_index += 1
        column_map.append(None if is_insertion else (spec or None))
    return column_map


def register_open_models_in_alignment_panel(session, *, reference_spec=None):
    """Build pairwise sequence alignment payloads for all open atomic structures
    and stash them in `session._codex_bridge_hit_alignments`.

    Use case: after running DALI / VAST / PDBeFold / US-align externally and
    opening the result PDBs in ChimeraX, this makes the sequence bar's
    alignment panel work for those structures too — same UX as Foldseek/native.

    `reference_spec` (e.g. '#1') overrides the auto-pick (first model = ref).
    """
    from chimerax.atomic import AtomicStructure, Residue

    chains = []
    for model in session.models.list(type=AtomicStructure):
        for chain in getattr(model, "chains", []):
            polymer_type = getattr(chain, "polymer_type", None)
            if polymer_type not in (Residue.PT_AMINO, Residue.PT_PROTEIN):
                continue
            try:
                seq = str(chain.ungapped()).upper().strip()
            except Exception:
                continue
            if not seq:
                continue
            mid = getattr(model, "id_string", "?")
            chains.append({
                "model": model,
                "chain": chain,
                "model_spec": f"#{mid}",
                "chain_id": getattr(chain, "chain_id", "?"),
                "spec": f"#{mid}/{getattr(chain, 'chain_id', '?')}",
                "name": getattr(model, "name", "structure"),
                "sequence": seq,
                "length": len(seq),
                "display": f"#{mid}/{getattr(chain, 'chain_id', '?')} · {getattr(model, 'name', 'structure')}",
            })
    if len(chains) < 2:
        return f"Alignment panel needs ≥2 open protein chains (found {len(chains)})."

    chains.sort(key=lambda c: (0 if reference_spec and c["spec"] == reference_spec else 1, -c["length"]))
    reference = chains[0]
    moving = chains[1:]
    session._codex_bridge_last_alignment_reference_spec = reference["model_spec"]
    session._codex_bridge_last_alignment_reference_chain_spec = reference["spec"]
    session._codex_bridge_last_alignment_reference_name = reference["name"]

    registry = getattr(session, "_codex_bridge_hit_alignments", None)
    if registry is None:
        registry = {}
        session._codex_bridge_hit_alignments = registry

    last_payload = None
    registered = 0
    for hit in moving:
        try:
            payload = _build_pairwise_alignment_payload(
                {
                    "model": reference["model"],
                    "chain": reference["chain"],
                    "model_spec": reference["model_spec"],
                    "sequence": reference["sequence"],
                    "spec": reference["spec"],
                    "display": reference["display"],
                },
                {
                    "model": hit["model"],
                    "chain": hit["chain"],
                    "model_spec": hit["model_spec"],
                    "sequence": hit["sequence"],
                    "spec": hit["spec"],
                    "display": hit["display"],
                },
            )
        except Exception:
            continue
        # Replace ungapped column maps with atomspec-aware ones (so clicks select).
        payload["reference"]["column_map"] = _column_map_from_chain_for_aligned(reference["chain"], payload["reference"]["aligned"])
        payload["moving"]["column_map"] = _column_map_from_chain_for_aligned(hit["chain"], payload["moving"]["aligned"])

        try:
            from chimerax.atomic import Sequence
            seqs = [
                Sequence(name=payload["reference"]["display"], characters=payload["reference"]["aligned"]),
                Sequence(name=payload["moving"]["display"], characters=payload["moving"]["aligned"]),
            ]
            alignment = session.alignments.new_alignment(
                seqs,
                identify_as=payload["alignment_id"],
                name=payload["name"],
                auto_associate=False,
                intrinsic=True,
            )
            payload["alignment_id"] = getattr(alignment, "ident", payload["alignment_id"])
        except Exception:
            pass

        for key in (
            reference["spec"], hit["spec"],
            reference["model_spec"], hit["model_spec"],
        ):
            if key:
                registry[key] = payload
        last_payload = payload
        registered += 1

    if last_payload is not None:
        session._codex_bridge_last_structure_alignment_payload = last_payload
        session._codex_bridge_last_alignment_id = last_payload.get("alignment_id")
        try:
            _publish_sequence_alignment_refresh(session)
        except Exception:
            pass

    return (
        f"Registered {registered} pairwise alignment(s) for the sequence panel "
        f"(reference: {reference['spec']} {reference['name']})."
    )


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
            _register_foldseek_hit_alignments(session, results, opened_models, set_name)
        except Exception as err:
            try:
                session.logger.warning(f"Foldseek: hit-alignment registration failed: {err}")
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


def launch_foldseek_open_aligned(session, *, count=3, database="pdb100", executor=None, model_hint=None):
    try:
        hit_count = max(1, min(50, int(count)))
    except Exception:
        hit_count = 3
    chains = _protein_chain_specs(session, model_hint=model_hint)
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


def launch_similar_open_aligned(session, *, count=3, database="pdb100", executor=None, model_hint=None):
    return launch_foldseek_open_aligned(session, count=count, database=database, executor=executor, model_hint=model_hint)


def _launch_structure_upload_site(session, *, label, site_key, url, fmt="pdb", executor=None, model_hint=None):
    structure_file, out_dir = _export_first_structure_file(
        session,
        fmt=fmt,
        prefix=f"chimerax_{site_key}",
        executor=executor,
        model_hint=model_hint,
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


def launch_dali_server(session, *, executor=None, model_hint=None):
    msg = _launch_structure_upload_site(
        session, label="DALI", site_key="dali", url=DALI_URL, fmt="pdb", executor=executor, model_hint=model_hint,
    )
    _start_downloads_watcher(session, tool="dali", label="DALI")
    return msg


def launch_vast_search(session, *, executor=None, model_hint=None):
    msg = _launch_structure_upload_site(
        session, label="NCBI VAST", site_key="vast", url=VAST_URL, fmt="pdb", executor=executor, model_hint=model_hint,
    )
    _start_downloads_watcher(session, tool="vast", label="VAST")
    return msg


def launch_pdbefold_search(session, *, executor=None, model_hint=None):
    msg = _launch_structure_upload_site(
        session, label="PDBeFold / SSM", site_key="pdbefold", url=PDBEFOLD_URL, fmt="pdb", executor=executor, model_hint=model_hint,
    )
    _start_downloads_watcher(session, tool="pdbefold", label="PDBeFold")
    return msg


def launch_pisa_server(session, *, executor=None, model_hint=None):
    return _launch_structure_upload_site(
        session,
        label="PDBePISA",
        site_key="pisa",
        url=PISA_URL,
        fmt="pdb",
        executor=executor,
        model_hint=model_hint,
    )


def launch_caver_server(session, *, executor=None, model_hint=None):
    return _launch_structure_upload_site(
        session,
        label="CAVER Web",
        site_key="caver",
        url=CAVER_URL,
        fmt="pdb",
        executor=executor,
        model_hint=model_hint,
    )


def launch_usalign(session, *, executor=None, entries=None):
    entries = list(entries or [])
    if not entries:
        entries = _atomic_model_entries(session, selected_preferred=True)
        if len(entries) < 2:
            entries = _atomic_model_entries(session, selected_preferred=False)
    if len(entries) >= 2:
        return launch_native_structure_alignment(session, entries=entries, executor=executor)

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
        _start_downloads_watcher(session, tool="usalign", label="US-align")
        return f"Opened US-align and uploaded {len(upload_files)} structure file(s) in Chrome."
    webbrowser.open(USALIGN_URL)
    _start_downloads_watcher(session, tool="usalign", label="US-align")
    return (
        f"Opened US-align. Exported {len(upload_files)} structures under {out_dir}; paths were copied to clipboard. "
        f"Automatic upload failed: {helper_error}"
    )


def launch_native_structure_alignment(session, *, entries=None, executor=None):
    entries = entries or _atomic_model_entries(session, selected_preferred=True)
    if len(entries) < 2:
        return "Native structural alignment needs at least two open/selected atomic structures."
    reference = entries[0]
    moving = entries[1:]
    session._codex_bridge_last_alignment_reference_spec = reference.get("spec")
    session._codex_bridge_last_alignment_reference_name = reference.get("name")
    commands = []
    failures = []
    for entry in moving:
        command = f"matchmaker {entry['spec']} to {reference['spec']}"
        try:
            _run_chimerax(session, command, executor=executor)
            commands.append(command)
        except Exception as err:
            fallback = f"align {entry['spec']} to {reference['spec']}"
            try:
                _run_chimerax(session, fallback, executor=executor)
                commands.append(fallback)
            except Exception as fallback_err:
                detail = str(fallback_err or err) or fallback_err.__class__.__name__
                failures.append(f"{entry['spec']}: {detail}")

    if len(entries) >= 2:
        hit_alignments = getattr(session, "_codex_bridge_hit_alignments", None)
        if hit_alignments is None:
            hit_alignments = {}
            session._codex_bridge_hit_alignments = hit_alignments
        last_payload = _build_multi_alignment_payload(entries) if len(entries) >= 3 else None
        if last_payload is not None:
            for spec_key in _alignment_payload_specs(last_payload):
                hit_alignments[spec_key] = last_payload
        for hit_entry in moving:
            try:
                payload = _build_pairwise_alignment_payload(reference, hit_entry)
            except Exception:
                continue
            try:
                from chimerax.atomic import Sequence
                seqs = [
                    Sequence(
                        name=payload["reference"]["display"],
                        characters=payload["reference"]["aligned"],
                    ),
                    Sequence(
                        name=payload["moving"]["display"],
                        characters=payload["moving"]["aligned"],
                    ),
                ]
                alignment = session.alignments.new_alignment(
                    seqs,
                    identify_as=payload["alignment_id"],
                    name=payload["name"],
                    auto_associate=False,
                    intrinsic=True,
                )
                payload["alignment_id"] = getattr(alignment, "ident", payload["alignment_id"])
            except Exception:
                pass
            # Tag both reference and moving spec so chain-switch lookup works
            # whether the user picks the receptor or one of the hits.
            for spec_key in (reference.get("spec"), hit_entry.get("spec")):
                if spec_key:
                    hit_alignments.setdefault(spec_key, payload)
            if last_payload is None:
                last_payload = payload
        if last_payload is not None:
            session._codex_bridge_last_structure_alignment_payload = last_payload
            session._codex_bridge_last_alignment_id = last_payload.get("alignment_id")
            _publish_sequence_alignment_refresh(session)

    files, out_dir = _export_model_entries(
        session,
        entries[: min(len(entries), 6)],
        fmt="pdb",
        prefix="chimerax_native_usalign",
        executor=executor,
    )
    usalign_report = _run_local_usalign_pairs(files, out_dir) if len(files) >= 2 else None
    lines = [
        "Native structure alignment prepared inside ChimeraX.",
        f"- reference: {reference['spec']} {reference['name']}",
        f"- aligned: {', '.join(entry['spec'] for entry in moving)}",
    ]
    if commands:
        lines.append("Executed ChimeraX commands:")
        lines.extend(f"- {command}" for command in commands)
    if failures:
        lines.append("Alignment warnings:")
        lines.extend(f"- {item}" for item in failures)
    if usalign_report:
        lines.append(usalign_report)
    else:
        lines.append("- US-align CLI not found; used ChimeraX matchmaker/align only.")
    return "\n".join(lines)


def _build_multi_alignment_payload(entries):
    seq_entries = []
    for entry in entries:
        try:
            seq_entry = _sequence_bar_entry_for_alignment(entry)
        except Exception:
            seq_entry = None
        if seq_entry is not None:
            seq_entries.append(seq_entry)
    if len(seq_entries) < 3:
        return None
    try:
        from .sequence_bar import _entries_multi_alignment_payload

        payload = _entries_multi_alignment_payload(seq_entries[0], seq_entries)
    except Exception:
        payload = None
    if payload is None:
        return None
    payload["name"] = "Codex structural multi alignment"
    payload["alignment_id"] = "codex_struct_multi_" + "_".join(
        str(entry.get("model_spec") or entry.get("spec") or "model")
        .replace("#", "")
        .replace("/", "_")
        .replace(".", "_")
        for entry in seq_entries[:8]
    )
    return payload


def _alignment_payload_specs(payload):
    specs = set()
    rows = list(payload.get("rows") or [])
    if not rows:
        rows = [payload.get("reference") or {}, payload.get("moving") or {}]
    for row in rows:
        for key in ("spec", "model_spec"):
            value = str(row.get(key) or "").strip()
            if value:
                specs.add(value)
        spec = str(row.get("spec") or "").strip()
        if "/" in spec:
            specs.add(spec.split("/", 1)[0])
    return specs


def _publish_sequence_alignment_refresh(session):
    try:
        from .sequence_bar import CodexSequenceBar

        bar = CodexSequenceBar.get_singleton(session, create=False, display=False)
    except Exception:
        bar = None
    if bar is None:
        return

    def refresh():
        try:
            bar.refresh()
        except Exception:
            pass

    try:
        session.ui.thread_safe(refresh)
    except Exception:
        refresh()


def _build_pairwise_alignment_payload(reference, moving):
    try:
        ref_entry = _sequence_bar_entry_for_alignment(reference)
        mov_entry = _sequence_bar_entry_for_alignment(moving)
        if ref_entry is not None and mov_entry is not None:
            from .sequence_bar import _entry_pair_alignment_payload

            payload = _entry_pair_alignment_payload(ref_entry, mov_entry)
            payload["name"] = "Codex structural pair alignment"
            return payload
    except Exception:
        pass

    ref_sequence = str(reference.get("sequence") or "")
    mov_sequence = str(moving.get("sequence") or "")
    aligned_ref, aligned_mov, ref_map, mov_map = _needleman_wunsch_align(ref_sequence, mov_sequence)
    alignment_id = f"codex_struct_{reference.get('spec', 'ref').replace('/', '_')}_{moving.get('spec', 'mov').replace('/', '_')}"
    return {
        "alignment_id": alignment_id,
        "name": "Codex structural pair alignment",
        "reference": {
            "spec": reference.get("spec", ""),
            "display": reference.get("display", reference.get("spec", "reference")),
            "sequence": ref_sequence,
            "aligned": aligned_ref,
            "column_map": ref_map,
        },
        "moving": {
            "spec": moving.get("spec", ""),
            "display": moving.get("display", moving.get("spec", "moving")),
            "sequence": mov_sequence,
            "aligned": aligned_mov,
            "column_map": mov_map,
        },
        "length": len(aligned_ref),
        "identity": _pairwise_identity(aligned_ref, aligned_mov),
    }


def _sequence_bar_entry_for_alignment(item):
    chain = item.get("chain")
    model = item.get("model") or getattr(chain, "structure", None)
    if chain is None and model is not None:
        chain = _primary_protein_chain_for_alignment(model)
    if chain is None:
        return None

    model = item.get("model") or getattr(chain, "structure", None)
    if model is None:
        return None
    model_spec = str(item.get("model_spec") or item.get("spec") or "")
    if "/" in model_spec:
        model_spec = model_spec.split("/", 1)[0]
    if not model_spec:
        model_spec = f"#{getattr(model, 'id_string', '?')}"
    model_name = item.get("name") or getattr(model, "name", "structure")

    from .sequence_bar import _entry_for_chain

    entry = _entry_for_chain(model_spec, model_name, chain)
    if not entry:
        return None
    return entry


def _primary_protein_chain_for_alignment(model):
    try:
        from chimerax.atomic import Residue
    except Exception:
        Residue = None

    candidates = []
    for chain in getattr(model, "chains", []) or []:
        polymer_type = getattr(chain, "polymer_type", None)
        if Residue is not None and polymer_type not in (Residue.PT_AMINO, Residue.PT_PROTEIN):
            continue
        residues = getattr(chain, "existing_residues", None)
        try:
            length = len(residues)
        except Exception:
            length = 0
        if length <= 0:
            continue
        candidates.append((length, str(getattr(chain, "chain_id", "") or ""), chain))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][2]


def _pairwise_identity(aligned_a, aligned_b):
    matches = 0
    aligned = 0
    for a, b in zip(aligned_a, aligned_b):
        if a == "-" or b == "-":
            continue
        aligned += 1
        if a == b:
            matches += 1
    return (matches / aligned) if aligned else 0.0


def _needleman_wunsch_align(seq_a, seq_b, match=2, mismatch=-1, gap=-2):
    a = str(seq_a or "")
    b = str(seq_b or "")
    n = len(a)
    m = len(b)

    score = [[0] * (m + 1) for _ in range(n + 1)]
    trace = [[None] * (m + 1) for _ in range(n + 1)]

    for i in range(1, n + 1):
        score[i][0] = i * gap
        trace[i][0] = "up"
    for j in range(1, m + 1):
        score[0][j] = j * gap
        trace[0][j] = "left"

    for i in range(1, n + 1):
        ca = a[i - 1]
        for j in range(1, m + 1):
            cb = b[j - 1]
            diag = score[i - 1][j - 1] + (match if ca == cb else mismatch)
            up = score[i - 1][j] + gap
            left = score[i][j - 1] + gap
            best = diag
            direction = "diag"
            if up > best:
                best = up
                direction = "up"
            if left > best:
                best = left
                direction = "left"
            score[i][j] = best
            trace[i][j] = direction

    aligned_a = []
    aligned_b = []
    map_a = []
    map_b = []
    i, j = n, m
    while i > 0 or j > 0:
        direction = trace[i][j] if i >= 0 and j >= 0 else None
        if direction == "diag" or (i > 0 and j > 0 and direction is None):
            aligned_a.append(a[i - 1])
            aligned_b.append(b[j - 1])
            map_a.append(i - 1)
            map_b.append(j - 1)
            i -= 1
            j -= 1
        elif direction == "up" or (i > 0 and j == 0):
            aligned_a.append(a[i - 1])
            aligned_b.append("-")
            map_a.append(i - 1)
            map_b.append(None)
            i -= 1
        else:
            aligned_a.append("-")
            aligned_b.append(b[j - 1])
            map_a.append(None)
            map_b.append(j - 1)
            j -= 1

    aligned_a.reverse()
    aligned_b.reverse()
    map_a.reverse()
    map_b.reverse()
    return "".join(aligned_a), "".join(aligned_b), map_a, map_b


def _run_local_usalign_pairs(files, out_dir):
    executable = None
    for name in ("USalign", "usalign", "TMalign", "tmalign"):
        executable = shutil.which(name)
        if executable:
            break
    if not executable:
        return None
    report_dir = Path(out_dir) / "usalign_reports"
    report_dir.mkdir(exist_ok=True)
    reference = files[0]
    summaries = []
    for index, moving in enumerate(files[1:], start=2):
        command = [executable, str(moving), str(reference)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
        report = (result.stdout or "") + ("\nSTDERR:\n" + result.stderr if result.stderr else "")
        report_path = report_dir / f"usalign_{index:02d}.txt"
        report_path.write_text(report, encoding="utf-8")
        if result.returncode != 0:
            summaries.append(f"{moving.name}: US-align exited {result.returncode}; report {report_path}")
            continue
        summary = _summarize_usalign_output(report)
        summaries.append(f"{moving.name}: {summary}; report {report_path}")
    if not summaries:
        return None
    return "\n".join(["Local US-align report:", *[f"- {item}" for item in summaries]])


def _summarize_usalign_output(text):
    lines = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if "TM-score=" in line or line.startswith("Aligned length=") or line.startswith("RMSD="):
            lines.append(" ".join(line.split()))
        if len(lines) >= 3:
            break
    return " | ".join(lines) if lines else "completed"


def launch_foldseek_foldmason(session, *, count=5, database="pdb100", executor=None, model_hint=None):
    try:
        hit_count = max(1, min(50, int(count)))
    except Exception:
        hit_count = 5
    if model_hint:
        entries = [entry for entry in _primary_atomic_model_entries(session) if entry.get("spec") == str(model_hint).strip()]
    else:
        entries = _atomic_model_entries(session, selected_preferred=True)
    if not entries:
        entries = _atomic_model_entries(session, selected_preferred=False)
    query_entry = entries[0] if entries else None

    chains = _protein_chain_specs(session, model_hint=model_hint)
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


def _launch_hmmer_page(session, model_hint=None):
    entries = _protein_chain_entries(session, model_hint=model_hint)
    if not entries:
        return "No protein chain sequence was resolved for HMMER."
    fasta = _fasta_text([entries[0]])
    _copy_text_to_clipboard(fasta)
    helper_error = _launch_with_browser_helper(["hmmer"], fasta)
    if helper_error is None:
        return f"Opened HMMER phmmer for {entries[0]['spec']} and filled the sequence in Chrome."
    webbrowser.open("https://www.ebi.ac.uk/Tools/hmmer/search/phmmer")
    return f"Opened HMMER phmmer for {entries[0]['spec']} and copied FASTA to the clipboard, but automatic page filling failed: {helper_error}"


def _launch_interpro_page(session, model_hint=None):
    entries = _protein_chain_entries(session, model_hint=model_hint)
    if not entries:
        return "No protein chain sequence was resolved for InterPro/Pfam."
    fasta = _fasta_text([entries[0]])
    _copy_text_to_clipboard(fasta)
    helper_error = _launch_with_browser_helper(["interpro"], fasta)
    if helper_error is None:
        return f"Opened InterPro/Pfam for {entries[0]['spec']} and filled the sequence in Chrome."
    webbrowser.open("https://www.ebi.ac.uk/interpro/search/sequence/")
    return f"Opened InterPro/Pfam for {entries[0]['spec']} and copied FASTA to the clipboard, but automatic page filling failed: {helper_error}"


def _launch_ncbi_page(session, model_hint=None):
    entries = _protein_chain_entries(session, model_hint=model_hint)
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


def _launch_hhpred_page(session, model_hint=None):
    entries = _protein_chain_entries(session, model_hint=model_hint)
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


def _launch_consurf_page(session, model_hint=None):
    entries = _protein_chain_entries(session, model_hint=model_hint)
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


def _launch_alphafold_server(session, model_hint=None):
    entries = _protein_chain_entries(session, model_hint=model_hint)
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

    if action == "ai-quick-analyze":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "Analyze")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "Analyze: cancelled")
            return

        def analyze_task(executor):
            from .builtin_actions import (
                _run,
                _run_catalytic_view,
            )
            from .named_selection import add_group, list_groups, remove_group
            from .semantic import (
                find_catalytic_triads,
                format_sequence_report,
                get_uniprot_feature_entries,
            )

            prior_managed_specs = list(getattr(session, "_codex_analyze_managed_specs", []) or [])
            session._codex_analyze_managed_specs = []
            for spec in prior_managed_specs:
                for cmd in (f"hide {spec} atoms", f"~label {spec}"):
                    try:
                        _run(session, cmd, executor=executor)
                    except Exception:
                        pass
            for cmd in ("select clear",):
                try:
                    _run(session, cmd, executor=executor)
                except Exception:
                    pass

            IMPORTANT_FEATURE_TYPES = {
                "Active site",
                "Metal binding",
                "Binding site",
                "Zinc finger",
            }
            CONDITIONAL_FEATURE_TYPES = {"Motif", "Site"}
            ANALYZE_FEATURE_KEEP_WORDS = (
                "active", "catalytic", "catalysis", "metal", "coordination",
                "binding", "binds", "substrate", "cofactor", "zinc", "calcium",
                "magnesium", "manganese", "iron", "copper", "nickel", "heme",
                "proton", "nucleophile", "charge relay",
            )
            ANALYZE_FEATURE_DROP_WORDS = (
                "glycosyl", "phosphoryl", "methyl", "acetyl", "ubiquitin",
                "lipid", "cleavage", "signal", "transit", "repeat",
                "low complexity", "compositionally biased",
            )
            from .display_color import choose_accent_color, choose_subtle_color
            triad_accent = choose_accent_color(session)
            site_subtle = choose_subtle_color(session)
            feature_colors = {
                "Active site": site_subtle,
                "Metal binding": site_subtle,
                "Binding site": site_subtle,
                "Zinc finger": site_subtle,
                "Motif": site_subtle,
                "Site": site_subtle,
                "triad": triad_accent,
            }
            COLOR_PRIORITY = (
                "Active site",
                "Metal binding",
                "Binding site",
                "Zinc finger",
                "Motif",
                "Site",
                "triad",
            )
            MAX_RESIDUE_SPAN = 6

            def analyze_feature_is_relevant(entry):
                ftype = str(entry.get("feature_type", "") or "")
                if ftype in IMPORTANT_FEATURE_TYPES:
                    return True
                if ftype not in CONDITIONAL_FEATURE_TYPES:
                    return False
                text = " ".join(
                    str(entry.get(key, "") or "")
                    for key in ("label", "description", "feature_type")
                ).lower()
                if any(word in text for word in ANALYZE_FEATURE_DROP_WORDS):
                    return False
                return any(word in text for word in ANALYZE_FEATURE_KEEP_WORDS)

            def analyze_triad_is_high_confidence(triad):
                specs = list(triad.get("specs") or [])
                if len(specs) < 3:
                    return False
                try:
                    nb = float(triad.get("nuc_base_distance", 99.0) or 99.0)
                    ba = float(triad.get("base_acid_distance", 99.0) or 99.0)
                except Exception:
                    return False
                return nb <= 3.8 and ba <= 3.8

            blocks = []
            grouped_specs = []
            if target_model_hint:
                blocks.append(f"Target structure: {target_model_hint}")

            entries = []
            try:
                entries = get_uniprot_feature_entries(session, model_hint=target_model_hint) or []
            except Exception as err:
                blocks.append(f"UniProt feature fetch failed: {err}")

            feature_lines = []
            for entry in entries:
                ftype = entry.get("feature_type", "")
                if not analyze_feature_is_relevant(entry):
                    continue
                span = (entry.get("end", 0) or 0) - (entry.get("start", 0) or 0) + 1
                if span > MAX_RESIDUE_SPAN:
                    continue
                spec = entry["spec"]
                slug = ftype.replace(" ", "_").lower()
                gname = f"{slug}_{entry['chain_id']}{entry['start']}"
                if entry["start"] != entry["end"]:
                    gname = f"{slug}_{entry['chain_id']}{entry['start']}_{entry['end']}"
                grouped_specs.append({
                    "name": gname,
                    "spec": spec,
                    "specs": [spec],
                    "category": ftype,
                    "label": entry.get("label", ""),
                    "start": entry.get("start"),
                    "end": entry.get("end"),
                    "single": entry["start"] == entry["end"],
                })
                feature_lines.append(
                    f"- {ftype} {spec}: {entry.get('label', '')}  → group `{gname}`"
                )

            triads = []
            try:
                triads = find_catalytic_triads(session, model_hint=target_model_hint) or []
            except Exception as err:
                blocks.append(f"Triad geometry scan failed: {err}")

            triad_lines = []
            triads = [triad for triad in triads if analyze_triad_is_high_confidence(triad)]
            for triad in triads[:4]:
                specs = triad.get("specs") or []
                if not specs:
                    continue
                names_short = "".join(
                    f"{r['name'][0]}{r['number']}" for r in triad.get("residues", [])
                )
                gname = f"triad_{names_short.lower()}"
                grouped_specs.append({
                    "name": gname,
                    "spec": " ".join(specs),
                    "specs": list(specs),
                    "category": "triad",
                    "label": triad.get("label", ""),
                })
                names = " · ".join(
                    f"{r['name']}{r['number']}" for r in triad.get("residues", [])
                )
                dist_parts = []
                if triad.get("nuc_base_distance") is not None:
                    dist_parts.append(f"nuc-base {triad['nuc_base_distance']:.2f}Å")
                if triad.get("base_acid_distance") is not None:
                    dist_parts.append(f"base-acid {triad['base_acid_distance']:.2f}Å")
                triad_lines.append(
                    f"- {triad['label']}: {names} ({', '.join(dist_parts) or 'geometry match'}) → group `{gname}`"
                )

            owned_specs = {}
            for category in COLOR_PRIORITY:
                color = feature_colors.get(category, "#bd9e6f")
                for grp in grouped_specs:
                    if grp["category"] != category:
                        continue
                    for spec in grp["specs"]:
                        owned_specs.setdefault(spec, color)

            current_group_names = set()
            seen_groups = set()
            for grp in grouped_specs:
                if grp["name"] in seen_groups:
                    continue
                seen_groups.add(grp["name"])
                member_colors = [owned_specs.get(s) for s in grp["specs"] if s in owned_specs]
                if not member_colors or len(set(member_colors)) != 1:
                    continue
                current_group_names.add(grp["name"])
                try:
                    group_color = feature_colors["triad"] if grp["category"] == "triad" else member_colors[0]
                    add_group(session, grp["name"], grp["spec"], color=group_color)
                except Exception:
                    pass

            analyze_prefixes = (
                "active_site_",
                "metal_binding_",
                "binding_site_",
                "motif_",
                "site_",
                "zinc_finger_",
                "triad_",
            )
            try:
                for group_name in list_groups(session):
                    if group_name not in current_group_names and group_name.startswith(analyze_prefixes):
                        remove_group(session, group_name)
            except Exception:
                pass

            colored_specs = set()
            for category in COLOR_PRIORITY:
                color = feature_colors.get(category, "#bd9e6f")
                for grp in grouped_specs:
                    if grp["category"] != category:
                        continue
                    if category == "triad":
                        fresh = list(grp["specs"])
                    else:
                        fresh = [s for s in grp["specs"] if s not in colored_specs]
                    if not fresh:
                        continue
                    fresh_join = " ".join(fresh)
                    try:
                        _run(session, f"select {fresh_join}", executor=executor)
                        _run(session, "show sel atoms", executor=executor)
                        _run(session, "style sel stick", executor=executor)
                        if category == "triad":
                            _run(session, f"color sel {color} target ab", executor=executor)
                        from .display_color import restore_charge_colors

                        restore_charge_colors(session, fresh_join)
                        _run(session, "select clear", executor=executor)
                    except Exception:
                        pass
                    colored_specs.update(fresh)

            label_specs = list(owned_specs.keys())[:24]
            if label_specs:
                try:
                    _run(session, "select " + " ".join(label_specs), executor=executor)
                    _run(session, "label sel residues", executor=executor)
                    _run(session, "select clear", executor=executor)
                except Exception:
                    pass

            if feature_lines:
                blocks.append(
                    f"UniProt key sites ({len(feature_lines)} — Active site / Metal / Binding / Motif / Zn finger):"
                )
                blocks.extend(feature_lines)
            if triad_lines:
                blocks.append(f"High-confidence catalytic triads detected ({len(triad_lines)}):")
                blocks.extend(triad_lines)

            if not feature_lines and not triad_lines:
                blocks.append(
                    "No high-confidence UniProt active/binding/metal feature or compact full catalytic triad "
                    "was found. Exploratory residue scoring is available from the Catalytic button."
                )

            try:
                seq = format_sequence_report(session, model_hint=target_model_hint)
                if seq:
                    blocks.append("Sequence summary:")
                    blocks.append(seq)
            except Exception:
                pass
            session._codex_analyze_managed_specs = list(owned_specs.keys())
            return "\n\n".join(blocks) if blocks else "No analyzable structure detected."
        _run_toolbar_chimerax_task(session, "Analyze", analyze_task)
        return
    elif action == "ai-quick-view":
        def view_task(executor):
            from .builtin_actions import _run_clean_publication_view

            return _run_clean_publication_view(
                session,
                executor=executor,
                preserve_existing=False,
                preserve_camera=True,
            )
        _run_toolbar_chimerax_task(session, "View", view_task)
        return
    elif action == "ai-quick-site":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "Pocket")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "Pocket: cancelled")
            return

        def site_task(executor):
            from .builtin_actions import _run
            from .display_color import restore_charge_colors
            from .named_selection import add_group, list_groups, remove_group
            from .semantic import find_kvfinder_pockets, get_ligand_sites, get_metal_sites, get_motif_hits

            def slug(text):
                value = re.sub(r"[^A-Za-z0-9]+", "_", str(text or "")).strip("_").lower()
                return value or "site"

            def residue_spec(model_spec, hit):
                return f"{model_spec}/{hit['chain_id']}:{int(hit['number'])}"

            donor_atoms = {
                "ASP": {"OD1", "OD2"},
                "GLU": {"OE1", "OE2"},
                "HIS": {"ND1", "NE2"},
                "CYS": {"SG"},
                "MET": {"SD"},
                "LYS": {"NZ"},
                "ASN": {"OD1", "ND2"},
                "GLN": {"OE1", "NE2"},
                "SER": {"OG"},
                "THR": {"OG1"},
            }
            direct_color = "#aab2bc"
            cluster_color = "#7c5497"
            motif_styles = {
                "metal": ("metal_predicted", "#b07e3a", "Motif-predicted metal/catalytic sites"),
                "nucleotide": ("nucleotide_predicted", "#5b8fb9", "Motif-predicted nucleotide-binding sites"),
                "phosphate": ("phosphate_predicted", "#bf6e7a", "Motif-predicted phosphate-binding sites"),
                "heme": ("heme_predicted", "#a04545", "Motif-predicted heme/electron-transfer sites"),
                "redox": ("redox_predicted", "#b89944", "Motif-predicted redox sites"),
            }

            def motif_category_key(hit):
                text = " ".join(
                    str(hit.get(key, ""))
                    for key in ("pattern_name", "category", "description")
                ).lower()
                if any(token in text for token in ("heme", "cytochrome", "electron")):
                    return "heme"
                pattern_name = str(hit.get("pattern_name", "")).lower()
                if "cxxc" in pattern_name or "thioredoxin" in text:
                    return "redox"
                if any(token in text for token in ("nucleotide", "rossmann", "walker", "p-loop", "atp", "nad")):
                    return "nucleotide"
                if "phosphate" in text or "hxxh" in text:
                    return "phosphate"
                if any(token in text for token in ("metal", "metal-binding", "metal/catalytic", "zinc", "calcium")):
                    return "metal"
                return None

            def residue_key(residue):
                return (str(residue.chain_id).strip() or "?", int(residue.number), str(residue.name).upper())

            def model_residue_map(model):
                mapping = {}
                for residue in getattr(model, "residues", []):
                    try:
                        mapping[(str(residue.chain_id).strip() or "?", int(residue.number))] = residue
                    except Exception:
                        continue
                return mapping

            def sidechain_donor_atoms(residue):
                allowed = donor_atoms.get(str(getattr(residue, "name", "")).upper(), set())
                atoms = []
                for atom in getattr(residue, "atoms", []):
                    if str(getattr(atom, "name", "")).upper() in allowed:
                        atoms.append(atom)
                return atoms

            def sidechain_centroid(residue):
                import numpy as np

                heavy = [
                    atom for atom in getattr(residue, "atoms", [])
                    if str(getattr(getattr(atom, "element", None), "name", "")).upper() != "H"
                ]
                sidechain = [
                    atom for atom in heavy
                    if str(getattr(atom, "name", "")).upper() not in {"N", "CA", "C", "O", "OXT"}
                ]
                atoms = sidechain or heavy
                if not atoms:
                    return None
                return np.array([atom.scene_coord for atom in atoms]).mean(axis=0)

            def residue_hit_from_atoms(residue, atoms, center):
                if not atoms:
                    return None
                import numpy as np

                distances = [float(np.linalg.norm(atom.scene_coord - center)) for atom in atoms]
                return {
                    "chain_id": str(residue.chain_id).strip() or "?",
                    "number": int(residue.number),
                    "name": str(residue.name).upper(),
                    "min_distance": min(distances),
                    "atom_names": {str(atom.name) for atom in atoms},
                }

            def residue_spec_from_residue(model_spec, residue):
                return f"{model_spec}/{str(residue.chain_id).strip() or '?'}:{int(residue.number)}"

            def motif_predicted_groups():
                from chimerax.atomic import AtomicStructure
                import numpy as np

                models = {
                    f"#{getattr(model, 'id_string', '?')}": model
                    for model in session.models.list(type=AtomicStructure)
                }
                residue_maps = {spec: model_residue_map(model) for spec, model in models.items()}
                groups = []
                category_counts = {}
                for hit in get_motif_hits(session) or []:
                    if target_model_hint and hit.get("model_spec") != target_model_hint:
                        continue
                    category_key = motif_category_key(hit)
                    if category_key is None:
                        continue
                    if category_counts.get(category_key, 0) >= 6:
                        continue
                    prefix, motif_color, category_title = motif_styles[category_key]
                    model_spec = hit.get("model_spec")
                    model = models.get(model_spec)
                    if model is None:
                        continue
                    chain_id = str(hit.get("chain_id", "")).strip() or "?"
                    residue_map = residue_maps.get(model_spec, {})
                    motif_residues = [
                        residue_map.get((chain_id, number))
                        for number in range(int(hit.get("start_number", 0)), int(hit.get("end_number", -1)) + 1)
                    ]
                    motif_residues = [residue for residue in motif_residues if residue is not None]
                    if not motif_residues:
                        continue
                    sidechain_centroids = [
                        centroid for centroid in (sidechain_centroid(residue) for residue in motif_residues)
                        if centroid is not None
                    ]
                    if category_key == "metal" and len(sidechain_centroids) >= 2:
                        pair_distances = [
                            float(np.linalg.norm(sidechain_centroids[i] - sidechain_centroids[j]))
                            for i in range(len(sidechain_centroids))
                            for j in range(i + 1, len(sidechain_centroids))
                        ]
                        if pair_distances and (sum(pair_distances) / len(pair_distances)) > 9.0:
                            continue
                    motif_atoms = [
                        atom
                        for residue in motif_residues
                        for atom in getattr(residue, "atoms", [])
                        if str(getattr(getattr(atom, "element", None), "name", "")).upper() != "H"
                    ]
                    if not motif_atoms:
                        continue
                    center = np.array([atom.scene_coord for atom in motif_atoms]).mean(axis=0)
                    motif_keys = {residue_key(residue) for residue in motif_residues}
                    hits = []
                    for residue in motif_residues:
                        donor_hit = residue_hit_from_atoms(residue, sidechain_donor_atoms(residue), center)
                        if donor_hit is not None and (category_key != "metal" or donor_hit["min_distance"] <= 5.0):
                            donor_hit["role"] = "motif"
                            hits.append(donor_hit)
                    nearby_hits = []
                    donor_cutoff = 5.0 if category_key == "metal" else 6.0
                    for residue in residue_map.values():
                        if residue_key(residue) in motif_keys:
                            continue
                        atoms = sidechain_donor_atoms(residue)
                        if not atoms:
                            continue
                        donor_hit = residue_hit_from_atoms(residue, atoms, center)
                        if donor_hit is not None and donor_hit["min_distance"] <= donor_cutoff:
                            donor_hit["role"] = "nearby"
                            nearby_hits.append(donor_hit)
                    nearby_hits.sort(key=lambda item: item["min_distance"])
                    if category_key == "metal" and not hits and not nearby_hits:
                        continue
                    hits.extend(nearby_hits[:8])
                    residue_specs = []
                    seen_specs = set()
                    for residue in motif_residues:
                        spec = residue_spec_from_residue(model_spec, residue)
                        if spec not in seen_specs:
                            residue_specs.append(spec)
                            seen_specs.add(spec)
                    for donor_hit in nearby_hits[:8]:
                        spec = residue_spec(model_spec, donor_hit)
                        if spec not in seen_specs:
                            residue_specs.append(spec)
                            seen_specs.add(spec)
                    if not residue_specs:
                        continue
                    label = hit.get("pattern_name") or "metal motif"
                    group_name = (
                        f"{prefix}_{slug(label)}_"
                        f"{slug(chain_id)}{int(hit.get('start_number', 0))}"
                    )
                    residues_text = (
                        f"{chain_id}:{int(hit.get('start_number', 0))}-"
                        f"{int(hit.get('end_number', 0))}"
                    )
                    groups.append(
                        {
                            "name": group_name,
                            "spec": " ".join(residue_specs),
                            "color": motif_color,
                            "label_offset": 0,
                            "category_key": category_key,
                            "category_title": category_title,
                            "title": f"{category_title}: {label} {residues_text}",
                            "lines": [
                                f"- category: {hit.get('category', category_key)}; residues: {residues_text}; group: {group_name}",
                                f"- {label} [{hit.get('matched_sequence', '')}]: "
                                + ", ".join(
                                    f"{item['name']} {item['chain_id']}{int(item['number'])} "
                                    f"{item['min_distance']:.2f} A via {','.join(sorted(item['atom_names']))}"
                                    for item in hits[:10]
                                )
                            ],
                        }
                    )
                    category_counts[category_key] = category_counts.get(category_key, 0) + 1
                return groups

            def donor_cluster_groups():
                from chimerax.atomic import AtomicStructure
                import numpy as np

                candidates = []
                for model in session.models.list(type=AtomicStructure):
                    model_spec = f"#{getattr(model, 'id_string', '?')}"
                    if target_model_hint and model_spec != target_model_hint:
                        continue
                    for residue in getattr(model, "residues", []):
                        atoms = sidechain_donor_atoms(residue)
                        if not atoms:
                            continue
                        for atom in atoms:
                            candidates.append(
                                {
                                    "model_spec": model_spec,
                                    "residue": residue,
                                    "atom": atom,
                                    "coord": atom.scene_coord,
                                }
                            )
                clusters = []
                used = set()
                for seed in candidates:
                    seed_key = residue_key(seed["residue"])
                    if (seed["model_spec"], seed_key) in used:
                        continue
                    local = []
                    local_residues = set()
                    for candidate in candidates:
                        if candidate["model_spec"] != seed["model_spec"]:
                            continue
                        key = (candidate["model_spec"], residue_key(candidate["residue"]))
                        if key in used:
                            continue
                        if float(np.linalg.norm(candidate["coord"] - seed["coord"])) <= 7.0:
                            local.append(candidate)
                            local_residues.add(key)
                    if len(local_residues) < 3:
                        continue
                    residue_best = {}
                    for item in local:
                        key = residue_key(item["residue"])
                        dist = float(np.linalg.norm(item["coord"] - seed["coord"]))
                        if key not in residue_best or dist < residue_best[key][0]:
                            residue_best[key] = (dist, item)
                    donors = [item for _dist, item in sorted(residue_best.values(), key=lambda pair: pair[0])]
                    if len(donors) < 3:
                        continue
                    coords = np.array([item["coord"] for item in donors])
                    pair_distances = [
                        float(np.linalg.norm(coords[i] - coords[j]))
                        for i in range(len(coords))
                        for j in range(i + 1, len(coords))
                    ]
                    if not pair_distances or min(pair_distances) > 7.0:
                        continue
                    closest_three = coords[:3]
                    three_span = max(
                        float(np.linalg.norm(closest_three[i] - closest_three[j]))
                        for i in range(3)
                        for j in range(i + 1, 3)
                    )
                    if three_span > 9.0:
                        continue
                    center = coords.mean(axis=0)
                    clusters.append(
                        {
                            "model_spec": seed["model_spec"],
                            "donors": donors,
                            "center": center,
                            "mean_pair_distance": sum(pair_distances) / len(pair_distances),
                            "used_keys": {(seed["model_spec"], residue_key(item["residue"])) for item in donors},
                        }
                    )
                    used.update(clusters[-1]["used_keys"])
                clusters.sort(key=lambda item: (-len(item["donors"]), item["mean_pair_distance"]))
                groups = []
                for cluster in clusters[:4]:
                    donors = cluster["donors"]
                    first_residue = donors[0]["residue"]
                    residue_specs = [
                        residue_spec_from_residue(cluster["model_spec"], item["residue"])
                        for item in donors
                    ]
                    group_name = (
                        f"metal_cluster_{slug(str(first_residue.chain_id).strip() or '?')}"
                        f"{int(first_residue.number)}"
                    )
                    lines = []
                    for item in donors:
                        residue = item["residue"]
                        distance = float(np.linalg.norm(item["coord"] - cluster["center"]))
                        lines.append(
                            f"{str(residue.name).upper()} {str(residue.chain_id).strip() or '?'}"
                            f"{int(residue.number)} {distance:.2f} A via {item['atom'].name}"
                        )
                    groups.append(
                        {
                            "name": group_name,
                            "spec": " ".join(residue_specs),
                            "color": cluster_color,
                            "label_offset": 0,
                            "title": f"Predicted donor cluster {str(first_residue.chain_id).strip() or '?'}{int(first_residue.number)}",
                            "lines": ["- donor centroid distances: " + ", ".join(lines)],
                        }
                    )
                return groups

            def kvfinder_binding_site_groups():
                try:
                    pockets = _call_ui_thread(
                        session,
                        lambda: find_kvfinder_pockets(
                            session,
                            model_hint=target_model_hint,
                            top_n=3,
                            lining_shell=5.0,
                            max_lining_shell=7.0,
                            min_lining_residues=12,
                            min_volume=60.0,
                            min_depth=0.8,
                            cleanup_models=False,
                            return_cavity_models=True,
                        ),
                        timeout=240,
                    )
                except Exception as err:
                    blocks.append(f"KVFinder pocket scan failed: {err}")
                    return []
                groups = []
                for rank, pocket in enumerate(pockets or [], start=1):
                    specs = list(pocket.get("lining_specs") or [])
                    if len(specs) < 5:
                        continue
                    tags = list(pocket.get("tags") or [])
                    label_token = "geometry"
                    if tags:
                        label_token = slug(tags[0].split(":", 1)[-1].strip().split()[0])
                    group_name = f"binding_pocket_{rank:02d}_{label_token}"
                    rank_score = float(pocket.get("rank_score", 0.0) or 0.0)
                    geometry_score = float(pocket.get("geometry_score", 0.0) or 0.0)
                    chemistry_score = float(pocket.get("chemistry_score", 0.0) or 0.0)
                    evidence_score = float(pocket.get("evidence_score", 0.0) or 0.0)
                    lining_shell = float(pocket.get("lining_shell", 0.0) or 0.0)
                    cavity_spec = str(pocket.get("cavity_model_spec") or "").strip()
                    volume = float(pocket.get("volume", 0.0) or 0.0)
                    max_depth = float(pocket.get("max_depth", 0.0) or 0.0)
                    tag_text = ", ".join(tags) if tags else "geometry-only"
                    groups.append(
                        {
                            "name": group_name,
                            "rank": rank,
                            "spec": " ".join(specs[:96]),
                            "color": "#5b8fb9",
                            "label_offset": 0,
                            "visual_mode": "pocket_volume",
                            "show_overlay": rank == 1,
                            "cavity_spec": cavity_spec,
                            "cavity_model": pocket.get("cavity_model"),
                            "cavity_group": pocket.get("cavity_group"),
                            "choice_label": (
                                f"#{rank}: score {rank_score:.2f}; volume {volume:.0f} A^3; "
                                f"depth {max_depth:.1f} A; residues {len(specs)}; tags {tag_text}"
                            ),
                            "title": f"Predicted binding pocket #{rank} (score {rank_score:.2f})",
                            "lines": [
                                (
                                    f"- KVFinder-ranked pocket: volume {volume:.0f} A^3; "
                                    f"depth {max_depth:.1f} A; "
                                    f"lining residues {len(specs)} at {lining_shell:.1f} A shell; group: {group_name}"
                                ),
                                (
                                    f"- score components: geometry {geometry_score:.2f}, chemistry {chemistry_score:.2f}, "
                                    f"evidence {evidence_score:.2f}; tags: {tag_text}"
                                ),
                                (
                                    f"- pocket volume overlay: {cavity_spec or 'unavailable'}; "
                                    + (
                                        "shown without changing protein display/color/labels"
                                        if rank == 1
                                        else "candidate listed, not displayed by default"
                                    )
                                ),
                            ],
                        }
                    )
                return groups

            prior_managed_specs = list(getattr(session, "_codex_site_managed_specs", []) or [])
            prior_managed_models = list(getattr(session, "_codex_site_managed_models", []) or [])
            session._codex_site_managed_specs = []
            session._codex_site_managed_models = []
            if prior_managed_models:
                unique_models = []
                seen_model_ids = set()
                for model in prior_managed_models:
                    if model is None or id(model) in seen_model_ids:
                        continue
                    seen_model_ids.add(id(model))
                    unique_models.append(model)
                try:
                    session.models.close(unique_models)
                except Exception:
                    pass
            for spec in prior_managed_specs:
                for cmd in (f"~label {spec}",):
                    try:
                        _run(session, cmd, executor=executor)
                    except Exception:
                        pass

            try:
                for group_name in list_groups(session):
                    if group_name.startswith((
                        "metal_coord_",
                        "ligand_cavity_",
                        "metal_predicted_",
                        "metal_cluster_",
                        "binding_pocket_",
                        "nucleotide_predicted_",
                        "phosphate_predicted_",
                        "heme_predicted_",
                        "redox_predicted_",
                    )):
                        remove_group(session, group_name)
            except Exception:
                pass

            blocks = [f"Target structure: {target_model_hint}"] if target_model_hint else []
            site_groups = []

            try:
                site_groups.extend(kvfinder_binding_site_groups())
            except Exception as err:
                blocks.append(f"KVFinder pocket scan failed: {err}")

            if site_groups:
                metal_sites = []
            else:
                try:
                    metal_sites = get_metal_sites(
                        session,
                        model_hint=target_model_hint,
                        direct_cutoff=3.0,
                        shell_cutoff=5.0,
                    ) or []
                except Exception as err:
                    metal_sites = []
                    blocks.append(f"Metal-site scan failed: {err}")

            for site in metal_sites[:6]:
                direct = []
                for hit in site.get("direct", []):
                    allowed = donor_atoms.get(str(hit.get("name", "")).upper())
                    if not allowed:
                        continue
                    atom_names = {str(a) for a in hit.get("atom_names", set())}
                    if atom_names & allowed:
                        direct.append(hit)
                if not direct:
                    continue
                direct = sorted(direct, key=lambda h: h.get("min_distance", 99))[:6]
                direct_keys = {
                    (str(h.get("chain_id")), int(h.get("number", 0)), str(h.get("name")))
                    for h in direct
                }
                second_shell = []
                for hit in site.get("catalytic_like", []):
                    key = (str(hit.get("chain_id")), int(hit.get("number", 0)), str(hit.get("name")))
                    if key in direct_keys:
                        continue
                    if float(hit.get("min_distance", 99)) <= 5.0:
                        second_shell.append(hit)
                second_shell = sorted(second_shell, key=lambda h: h.get("min_distance", 99))[:8]
                model_spec = site["model_spec"]
                residue_specs = [residue_spec(model_spec, hit) for hit in [*direct, *second_shell]]
                specs = [site["metal_spec"], *residue_specs]
                group_name = f"metal_coord_{slug(site.get('metal_label'))}"
                site_groups.append(
                    {
                        "name": group_name,
                        "spec": " ".join(specs),
                        "color": direct_color,
                        "color_residues": False,
                        "label_offset": 1,
                        "title": f"Metal coordination shell {site.get('metal_label', '')}",
                        "lines": [
                            f"- {site.get('metal_label', 'metal')}: "
                            + ", ".join(
                                f"{h['name']} {h['chain_id']}{int(h['number'])} "
                                f"{h['min_distance']:.2f} A via {','.join(sorted(h['atom_names']))}"
                                for h in direct
                            )
                        ],
                    }
                )

            if not site_groups:
                try:
                    ligand_sites = get_ligand_sites(
                        session,
                        model_hint=target_model_hint,
                        shell_cutoff=4.5,
                    ) or []
                except Exception as err:
                    ligand_sites = []
                    blocks.append(f"Ligand-pocket scan failed: {err}")

                for site in ligand_sites[:6]:
                    nearby = list(site.get("nearby", []))[:12]
                    if not nearby:
                        continue
                    model_spec = site["model_spec"]
                    residue_specs = [residue_spec(model_spec, hit) for hit in nearby]
                    specs = [site["ligand_spec"], *residue_specs]
                    group_name = f"ligand_cavity_{slug(site.get('ligand_label'))}"
                    site_groups.append(
                        {
                            "name": group_name,
                            "spec": " ".join(specs),
                            "color": direct_color,
                            "color_residues": False,
                            "label_offset": 1,
                            "title": f"Ligand pocket {site.get('ligand_label', '')}",
                            "lines": [
                                f"- {site.get('ligand_label', 'ligand')}: "
                                + ", ".join(
                                    f"{h['name']} {h['chain_id']}{int(h['number'])} "
                                    f"{h['min_distance']:.2f} A"
                                    for h in nearby[:8]
                                )
                            ],
                        }
                    )

            if not site_groups:
                try:
                    site_groups.extend(motif_predicted_groups())
                except Exception as err:
                    blocks.append(f"Motif-site prediction failed: {err}")

            if not site_groups:
                try:
                    site_groups.extend(donor_cluster_groups())
                except Exception as err:
                    blocks.append(f"Donor-cluster scan failed: {err}")

            pocket_groups = [
                group for group in site_groups
                if group.get("visual_mode") == "pocket_volume"
            ]
            selected_pocket_ranks = []
            if pocket_groups:
                forced_selection = getattr(session, "_codex_pocket_force_selection", None)
                if forced_selection is not None:
                    try:
                        delattr(session, "_codex_pocket_force_selection")
                    except Exception:
                        pass
                    if isinstance(forced_selection, str):
                        selected_pocket_ranks = [
                            int(token)
                            for token in re.split(r"[\s,]+", forced_selection.strip())
                            if token.isdigit()
                        ]
                    elif isinstance(forced_selection, (list, tuple, set)):
                        selected_pocket_ranks = [
                            int(rank)
                            for rank in forced_selection
                            if str(rank).strip().isdigit()
                        ]
                    else:
                        try:
                            selected_pocket_ranks = [int(forced_selection)]
                        except Exception:
                            selected_pocket_ranks = []
                else:
                    selected_pocket_ranks = _call_ui_thread(
                        session,
                        lambda: _prompt_overlay_candidate_selection(
                            session,
                            pocket_groups,
                            title="Pocket candidates",
                            label="KVFinder ranked several pockets. Select the overlay(s) to show in 3D:",
                            default_ranks=(1,),
                            allow_multiple=True,
                        ),
                        timeout=300,
                    )
                if selected_pocket_ranks is None:
                    for group in pocket_groups:
                        model = group.get("cavity_model")
                        if model is not None:
                            try:
                                session.models.close([model])
                            except Exception:
                                pass
                    return "Pocket selection cancelled."
                valid_pocket_ranks = {int(group.get("rank", index + 1) or (index + 1)) for index, group in enumerate(pocket_groups)}
                selected_set = {
                    int(rank) for rank in selected_pocket_ranks
                    if int(rank) in valid_pocket_ranks
                }
                if not selected_set:
                    selected_set = {int(pocket_groups[0].get("rank", 1) or 1)}
                for index, group in enumerate(pocket_groups, start=1):
                    rank = int(group.get("rank", index) or index)
                    group["show_overlay"] = rank in selected_set
                selected_pocket_ranks = sorted(selected_set)

            for group in site_groups:
                try:
                    add_group(session, group["name"], group["spec"], color=group["color"])
                except Exception:
                    pass

            managed_overlay_models = []
            displayed_site_groups = []
            for group in site_groups:
                if group.get("visual_mode") != "pocket_volume":
                    continue
                cavity_spec = str(group.get("cavity_spec") or "").strip()
                if not cavity_spec:
                    continue
                if not group.get("show_overlay", False):
                    try:
                        model = group.get("cavity_model")
                        if model is not None:
                            session.models.close([model])
                    except Exception:
                        pass
                    continue
                displayed_site_groups.append(group)
                cavity_group = group.get("cavity_group")
                managed_overlay_models.append(cavity_group if cavity_group is not None else group.get("cavity_model"))
                try:
                    _run(session, f"show {cavity_spec} atoms", executor=executor)
                except Exception:
                    pass
                try:
                    _run(session, f"style {cavity_spec} sphere", executor=executor)
                    _run(session, f"color {cavity_spec} {group['color']} target a", executor=executor)
                    _run(session, f"transparency {cavity_spec} 45 target a", executor=executor)
                except Exception:
                    pass
                try:
                    _run(session, f"surface {cavity_spec}", executor=executor)
                    _run(session, f"color {cavity_spec} {group['color']} target s", executor=executor)
                    _run(session, f"transparency {cavity_spec} 65 target s", executor=executor)
                except Exception:
                    pass

            if site_groups:
                first_group_name = site_groups[0]["name"]
                if first_group_name.startswith("metal_coord_"):
                    mode = "Metal coordination shells"
                elif first_group_name.startswith("ligand_cavity_"):
                    mode = "Ligand-binding cavities"
                elif first_group_name.startswith("binding_pocket_"):
                    mode = "KVFinder-ranked binding pockets"
                elif first_group_name.startswith((
                    "metal_predicted_",
                    "nucleotide_predicted_",
                    "phosphate_predicted_",
                    "heme_predicted_",
                    "redox_predicted_",
                )):
                    category_titles = []
                    for group in site_groups:
                        title = group.get("category_title")
                        if title and title not in category_titles:
                            category_titles.append(title)
                    mode = ", ".join(category_titles) or "Motif-predicted sites"
                else:
                    mode = "Spatial donor clusters"
                if first_group_name.startswith("binding_pocket_"):
                    selected_text = ", ".join(f"#{rank}" for rank in selected_pocket_ranks) or "#1"
                    blocks.insert(
                        0,
                        f"{mode}: {len(site_groups)} candidate(s) ranked; showing selected overlay(s) "
                        f"{selected_text}; selected lining residues shown as sticks.",
                    )
                else:
                    blocks.insert(0, f"{mode} registered ({len(site_groups)} group(s)); top candidate shown as sticks.")
                groups_to_show = displayed_site_groups or [site_groups[0]]
                first_spec = str(groups_to_show[0].get("spec", "")).strip()
                first_cavity_spec = str(groups_to_show[0].get("cavity_spec", "") or "").strip()
                focus_spec = first_cavity_spec or first_spec
                for show_group in groups_to_show:
                    show_spec = str(show_group.get("spec", "")).strip()
                    if not show_spec:
                        continue
                    for command in (
                        f"show {show_spec} atoms",
                        f"style {show_spec} stick",
                    ):
                        try:
                            _run(session, command, executor=executor)
                        except Exception:
                            pass
                    try:
                        restore_charge_colors(session, show_spec)
                    except Exception:
                        pass
                if focus_spec:
                    blocks.insert(1, f"Selected overlay added without changing camera: {focus_spec}")
                for group in site_groups:
                    blocks.append(group["title"])
                    blocks.extend(group["lines"])
                session._codex_site_managed_specs = [
                    spec
                    for group in site_groups
                    for spec in str(group.get("spec", "")).split()
                ]
                session._codex_site_managed_models = managed_overlay_models
                return "\n".join(blocks)
            attempts = (
                "No site candidates detected after trying KVFinder-ranked geometry pockets, "
                "bound metal coordination shells, bound ligand cavities, motif predictions, "
                "and spatial Asp/Glu/His/Cys donor clusters."
            )
            return "\n".join([attempts, *blocks]) if blocks else attempts
        _run_toolbar_chimerax_task(session, "Pocket", site_task)
        return
    elif action == "ai-quick-cavity":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "Cavity")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "Cavity: cancelled")
            return

        cached = getattr(session, "_codex_cavity_last_params", None)
        if cached and isinstance(cached, tuple) and len(cached) == 2:
            default_distance, default_transparency = cached
        else:
            default_distance, default_transparency = 5.0, 65
        try:
            default_count = max(1, min(6, int(getattr(session, "_codex_cavity_pocket_count", 5) or 5)))
        except Exception:
            default_count = 5
        try:
            default_min_volume = max(1.0, min(10000.0, float(getattr(session, "_codex_cavity_min_volume", 30.0) or 30.0)))
        except Exception:
            default_min_volume = 30.0
        try:
            default_min_depth = max(0.0, min(50.0, float(getattr(session, "_codex_cavity_min_depth", 1.0) or 1.0)))
        except Exception:
            default_min_depth = 1.0

        forced_options = getattr(session, "_codex_cavity_force_options", None)
        if isinstance(forced_options, dict):
            try:
                delattr(session, "_codex_cavity_force_options")
            except Exception:
                pass
            cavity_options = {
                "distance": forced_options.get("distance", default_distance),
                "transparency": forced_options.get("transparency", default_transparency),
                "count": forced_options.get("count", forced_options.get("scan_count", default_count)),
                "min_volume": forced_options.get("min_volume", default_min_volume),
                "min_depth": forced_options.get("min_depth", default_min_depth),
            }
            if "selected_ranks" in forced_options:
                session._codex_cavity_force_selection = forced_options.get("selected_ranks")
        else:
            cavity_options = _prompt_cavity_params(
                session,
                default_distance=default_distance,
                default_transparency=default_transparency,
                default_count=default_count,
                default_min_volume=default_min_volume,
                default_min_depth=default_min_depth,
            )
        if cavity_options is None:
            _report_toolbar_result(session, "Cavity cancelled.")
            return

        cavity_distance = float(cavity_options.get("distance", default_distance))
        cavity_transparency = int(cavity_options.get("transparency", default_transparency))
        cavity_candidate_count = max(1, min(6, int(cavity_options.get("count", default_count) or default_count)))
        cavity_min_volume = max(1.0, min(10000.0, float(cavity_options.get("min_volume", default_min_volume) or default_min_volume)))
        cavity_min_depth = max(0.0, min(50.0, float(cavity_options.get("min_depth", default_min_depth) or default_min_depth)))
        session._codex_cavity_last_params = (float(cavity_distance), int(cavity_transparency))
        session._codex_cavity_pocket_count = cavity_candidate_count
        session._codex_cavity_min_volume = cavity_min_volume
        session._codex_cavity_min_depth = cavity_min_depth

        def cavity_task(executor):
            from .builtin_actions import _run
            from .display_color import restore_charge_colors
            from .named_selection import add_group, list_groups, remove_group
            from .semantic import (
                find_apo_binding_pocket,
                find_kvfinder_pockets,
                get_ligand_sites,
            )

            shell_distance = float(cavity_distance)
            transparency_pct = int(cavity_transparency)
            candidate_count = int(cavity_candidate_count)

            cavity_palette = [
                "#ff9d5a",  # warm peach (highest volume)
                "#ffd166",  # amber
                "#06d6a0",  # mint
                "#118ab2",  # teal
                "#a36bd9",  # violet
            ]

            def slug(text):
                value = re.sub(r"[^A-Za-z0-9]+", "_", str(text or "")).strip("_").lower()
                return value or "pocket"

            def residue_spec(model_spec, hit):
                return f"{model_spec}/{hit['chain_id']}:{int(hit['number'])}"

            def reset_prior_cavity():
                prior_specs = list(getattr(session, "_codex_cavity_managed_specs", []) or [])
                legacy_spec = getattr(session, "_codex_bridge_cavity_spec", "")
                if legacy_spec and legacy_spec not in prior_specs:
                    prior_specs.append(legacy_spec)
                for candidate in list(getattr(session, "_codex_cavity_candidates", []) or []):
                    spec = str(candidate.get("spec") or "").strip() if isinstance(candidate, dict) else ""
                    if spec and spec not in prior_specs:
                        prior_specs.append(spec)
                session._codex_cavity_managed_specs = []
                session._codex_cavity_candidates = []
                session._codex_cavity_selected_ranks = []
                for prior_spec in prior_specs:
                    for cmd in (f"~surface {prior_spec}", f"~label {prior_spec}"):
                        try:
                            _run(session, cmd, executor=executor)
                        except Exception:
                            pass
                try:
                    for group_name in list_groups(session):
                        if group_name.startswith("cavity_"):
                            remove_group(session, group_name)
                except Exception:
                    pass
                session._codex_bridge_cavity_spec = ""

            def kvfinder_pockets():
                try:
                    pockets = _call_ui_thread(
                        session,
                        lambda: find_kvfinder_pockets(
                            session,
                            model_hint=target_model_hint,
                            top_n=candidate_count,
                            lining_shell=shell_distance,
                            min_volume=cavity_min_volume,
                            min_depth=cavity_min_depth,
                        ),
                        timeout=240,
                    )
                except Exception as err:
                    try:
                        session.logger.warning(f"KVFinder cavity detection failed: {err}")
                    except Exception:
                        pass
                    return []
                results = []
                for index, pocket in enumerate(pockets, start=1):
                    specs = list(pocket.get("lining_specs") or [])
                    if not specs:
                        continue
                    tags = pocket.get("tags") or []
                    ligand_tag = next((t for t in tags if t.startswith("ligand:")), None)
                    metal_tag = next((t for t in tags if t.startswith("metal:")), None)
                    triad_tag = next((t for t in tags if t.startswith("catalytic")), None)
                    descriptor = "geometric pocket"
                    label_token = "geom"
                    if ligand_tag:
                        descriptor = ligand_tag
                        label_token = slug(ligand_tag.split(":", 1)[1].strip().split()[0] if ":" in ligand_tag else "ligand")
                    elif metal_tag:
                        descriptor = metal_tag
                        label_token = slug(metal_tag.split(":", 1)[1].strip())
                    elif triad_tag:
                        descriptor = "catalytic site"
                        label_token = "triad"
                    elif tags:
                        descriptor = tags[0]
                        label_token = slug(tags[0])
                    results.append({
                        "rank": index,
                        "spec": " ".join(specs),
                        "specs_list": specs,
                        "tags": tags,
                        "descriptor": descriptor,
                        "color": cavity_palette[(index - 1) % len(cavity_palette)],
                        "group_name": f"cavity_{index:02d}_{label_token}",
                        "residue_count": len(specs),
                        "volume": pocket.get("volume"),
                        "max_depth": pocket.get("max_depth"),
                        "rank_score": pocket.get("rank_score"),
                        "geometry_score": pocket.get("geometry_score"),
                        "chemistry_score": pocket.get("chemistry_score"),
                        "evidence_score": pocket.get("evidence_score"),
                        "choice_label": (
                            f"#{index}: score {float(pocket.get('rank_score', 0.0) or 0.0):.2f}; "
                            f"volume {float(pocket.get('volume', 0.0) or 0.0):.0f} A^3; "
                            f"depth {float(pocket.get('max_depth', 0.0) or 0.0):.1f} A; "
                            f"residues {len(specs)}; tags {', '.join(tags) if tags else 'geometry-only'}"
                        ),
                    })
                return results

            def ligand_fallback():
                ligand_sites = get_ligand_sites(
                    session,
                    model_hint=target_model_hint,
                    shell_cutoff=shell_distance,
                ) or []
                fallback = []
                for site in ligand_sites[:5]:
                    nearby = [
                        hit for hit in site.get("nearby", [])
                        if len(hit.get("atom_names", ())) >= 2
                    ]
                    if not nearby:
                        continue
                    nearby.sort(
                        key=lambda hit: (
                            -int(hit.get("contact_count", len(hit.get("atom_names", ())))),
                            float(hit.get("min_distance", 99.0)),
                        )
                    )
                    nearby = nearby[:35]
                    model_spec = site["model_spec"]
                    specs = []
                    seen = set()
                    for hit in nearby:
                        spec = residue_spec(model_spec, hit)
                        if spec not in seen:
                            specs.append(spec)
                            seen.add(spec)
                    if specs:
                        ligand_label = site.get("ligand_label", "ligand")
                        fallback.append({
                            "rank": len(fallback) + 1,
                            "spec": " ".join(specs),
                            "specs_list": specs,
                            "tags": [f"ligand: {ligand_label}"],
                            "descriptor": f"ligand: {ligand_label}",
                            "color": cavity_palette[len(fallback) % len(cavity_palette)],
                            "group_name": f"cavity_lig_{slug(ligand_label)}",
                            "residue_count": len(specs),
                        })
                return fallback

            def apo_fallback():
                specs, heuristic = find_apo_binding_pocket(session)
                if len(specs) < 6:
                    return []
                first = specs[0]
                chain_start = "x"
                if "/" in first and ":" in first:
                    chain_part = first.split("/", 1)[1]
                    chain_id = chain_part.split(":", 1)[0]
                    numbers = []
                    for spec in specs:
                        try:
                            spec_chain, spec_number = spec.split("/", 1)[1].split(":", 1)
                            if spec_chain == chain_id:
                                numbers.append(int(spec_number))
                        except Exception:
                            pass
                    chain_start = f"{slug(chain_id)}{min(numbers) if numbers else 'x'}"
                return [{
                    "rank": 1,
                    "spec": " ".join(specs),
                    "specs_list": specs,
                    "tags": [heuristic],
                    "descriptor": f"apo prediction ({heuristic})",
                    "color": cavity_palette[0],
                    "group_name": f"cavity_apo_{chain_start}",
                    "residue_count": len(specs),
                }]

            reset_prior_cavity()

            pockets = kvfinder_pockets()
            strategy_label = "KVFinder + motif evidence"
            if not pockets:
                pockets = ligand_fallback()
                strategy_label = "bound ligand shell"
            if not pockets:
                pockets = apo_fallback()
                strategy_label = "apo heuristic"
            if not pockets:
                message = getattr(
                    session,
                    "_codex_bridge_ligand_filter_message",
                    "No cavities detected — try opening a structure with bound ligand or set a higher contact distance.",
                ) or "No cavities detected — try opening a structure with bound ligand or set a higher contact distance."
                try:
                    session.logger.status(message)
                    session.logger.info(message)
                except Exception:
                    pass
                return message

            forced_selection = getattr(session, "_codex_cavity_force_selection", None)
            if forced_selection is not None:
                try:
                    delattr(session, "_codex_cavity_force_selection")
                except Exception:
                    pass
                if isinstance(forced_selection, str):
                    selected_ranks = [
                        int(token)
                        for token in re.split(r"[\s,]+", forced_selection.strip())
                        if token.isdigit()
                    ]
                elif isinstance(forced_selection, (list, tuple, set)):
                    selected_ranks = [
                        int(rank)
                        for rank in forced_selection
                        if str(rank).strip().isdigit()
                    ]
                else:
                    try:
                        selected_ranks = [int(forced_selection)]
                    except Exception:
                        selected_ranks = []
            else:
                selected_ranks = _call_ui_thread(
                    session,
                    lambda: _prompt_overlay_candidate_selection(
                        session,
                        pockets,
                        title="Cavity candidates",
                        label="Cavities ranked by geometry and residue evidence. Select overlay(s) to show in 3D:",
                        default_ranks=(1,),
                        allow_multiple=True,
                    ),
                    timeout=300,
                )
            if selected_ranks is None:
                return "Cavity selection cancelled."
            valid_ranks = {int(pocket.get("rank", index + 1) or (index + 1)) for index, pocket in enumerate(pockets)}
            selected_set = {
                int(rank) for rank in selected_ranks
                if int(rank) in valid_ranks
            }
            if not selected_set:
                selected_set = {int(pockets[0].get("rank", 1) or 1)}
            selected_pockets = [
                pocket for index, pocket in enumerate(pockets, start=1)
                if int(pocket.get("rank", index) or index) in selected_set
            ]
            for pocket in pockets:
                try:
                    add_group(session, pocket["group_name"], pocket["spec"], color=pocket["color"])
                except Exception:
                    pass

            managed_specs = []
            applied = []
            for pocket in selected_pockets:
                pocket_spec = pocket["spec"]
                color_hex = pocket["color"]
                try:
                    _run(session, f"surface {pocket_spec}", executor=executor)
                    _run(session, f"color {pocket_spec} {color_hex} target s", executor=executor)
                    _run(session, f"transparency {pocket_spec} {transparency_pct} target s", executor=executor)
                    restore_charge_colors(session, pocket_spec)
                except Exception as err:
                    try:
                        session.logger.warning(f"Cavity {pocket['rank']} overlay failed: {err}")
                    except Exception:
                        pass
                    continue
                managed_specs.append(pocket_spec)
                applied.append(pocket)

            if not applied:
                return "Cavity overlay failed for every detected pocket."

            session._codex_bridge_cavity_spec = applied[0]["spec"]
            session._codex_cavity_managed_specs = managed_specs
            session._codex_cavity_candidates = pockets
            session._codex_cavity_selected_ranks = sorted(selected_set)
            session._codex_cavity_transparency = transparency_pct
            session._codex_cavity_shell_distance = shell_distance
            session._codex_cavity_strategy_label = strategy_label

            try:
                from .cavity_browser import open_cavity_browser

                _call_ui_thread(
                    session,
                    lambda: open_cavity_browser(
                        session,
                        pockets,
                        selected_ranks=sorted(selected_set),
                        transparency=transparency_pct,
                        shell_distance=shell_distance,
                        strategy_label=strategy_label,
                    ),
                    timeout=30,
                )
            except Exception as err:
                try:
                    session.logger.warning(f"Cavity browser could not open: {err}")
                except Exception:
                    pass

            selected_text = ", ".join(f"#{rank}" for rank in sorted(selected_set))
            lines = [
                f"Target structure: {target_model_hint}" if target_model_hint else "Target structure: current default",
                f"{strategy_label}: {len(pockets)} candidate(s) ranked; showing {selected_text} "
                f"({transparency_pct}% transparent, contact ≤ {shell_distance:.1f} Å)."
            ]
            for pocket in pockets:
                vol_text = ""
                if pocket.get("volume") is not None:
                    vol_text = f" vol={pocket['volume']:.0f} Å³"
                if pocket.get("max_depth") is not None:
                    vol_text += f" depth={pocket['max_depth']:.1f} Å"
                if pocket.get("rank_score") is not None:
                    vol_text += f" score={float(pocket['rank_score']):.2f}"
                tag_text = ", ".join(pocket["tags"]) if pocket["tags"] else "no motif evidence"
                shown_text = "shown" if int(pocket.get("rank", 0) or 0) in selected_set else "hidden"
                lines.append(
                    f"- #{pocket['rank']} ({pocket['color']}, {shown_text}): {pocket['residue_count']} residues{vol_text} | {tag_text} | group {pocket['group_name']}"
                )
            return "\n".join(lines)

        _run_toolbar_chimerax_task(session, "Cavity", cavity_task)
        return
    elif action == "ai-quick-figure":
        from .tool import CodexAssistant

        assistant = CodexAssistant.get_singleton(session)
        if assistant is None:
            _report_toolbar_result(session, "Figure: AI Assistant unavailable", error=True)
            return
        try:
            assistant.display(True)
            assistant._show_assistant_tab()
            assistant._quick_apply_best_figure()
        except Exception as err:
            message = str(err) if str(err) else err.__class__.__name__
            _report_toolbar_result(session, f"Figure: {message}", error=True)
        return

    if action == "ai-analysis-blast":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "BLAST")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "BLAST: cancelled")
            return

        def blast_task(executor):
            from .builtin_actions import _run_blast_tool

            return _run_blast_tool(session, target_model_hint or "", executor=executor)

        _run_toolbar_chimerax_task(session, "Blast", blast_task)
        return
    elif action == "ai-analysis-alphafold":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "AlphaFold")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "AlphaFold: cancelled")
            return

        def alphafold_task(executor):
            from .builtin_actions import _run_alphafold_tool

            return _run_alphafold_tool(session, target_model_hint or "", executor=executor)

        _run_toolbar_chimerax_task(session, "AlphaFold", alphafold_task)
        return
    elif action == "ai-analysis-similar":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "Similar")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "Similar: cancelled")
            return

        _run_toolbar_chimerax_task(
            session,
            "Similar",
            lambda executor: launch_similar_open_aligned(session, executor=executor, model_hint=target_model_hint),
        )
        return
    elif action == "ai-analysis-profile":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "Profile")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "Profile: cancelled")
            return
        _run_toolbar_task(session, "Profile", lambda: _launch_uniprot_blast_page(session, model_hint=target_model_hint))
        return
    elif action == "ai-analysis-hhpred":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "HHpred")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "HHpred: cancelled")
            return
        _run_toolbar_task(session, "HHpred", lambda: _launch_hhpred_page(session, model_hint=target_model_hint))
        return
    elif action == "ai-analysis-signalp":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "SignalP")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "SignalP: cancelled")
            return
        # Web-first mode: open SignalP-6.0 page, paste sequence, copy FASTA
        # to clipboard, attempt auto-fill+submit. If it fails the user still
        # has the FASTA on the clipboard and the page open in their browser.
        from .signalp import launch_signalp_web

        def signalp_task():
            return launch_signalp_web(session, model_hint=target_model_hint)

        _run_toolbar_task(session, "SignalP web", signalp_task)
        return
    elif action == "ai-analysis-catalytic":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "Catalytic")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "Catalytic: cancelled")
            return

        def catalytic_task(executor):
            from .builtin_actions import _run_catalytic_view

            return _run_catalytic_view(
                session,
                model_hint=target_model_hint,
                executor=executor,
                preserve_existing=True,
            )

        _run_toolbar_chimerax_task(session, "Catalytic", catalytic_task)
        return
    elif action == "ai-analysis-interface":
        # Reuse the CodexAssistant picker dialog so the toolbar entry and the
        # Figure -> Interface mode share one targets+cutoff prompt.
        try:
            from .tool import CodexAssistant

            assistant = CodexAssistant.get_singleton(session)
        except Exception:
            assistant = None
        if assistant is None:
            _report_toolbar_result(session, "Interface: AI Assistant unavailable")
            return
        targets = assistant._prompt_interface_targets()
        if targets is None:
            _report_toolbar_result(session, "Interface: cancelled")
            return
        session._codex_interface_enzyme_spec = targets["enzyme_spec"]
        session._codex_interface_ligand_spec = targets["ligand_spec"]
        session._codex_interface_cutoff = targets["cutoff"]

        def interface_task(executor):
            from .builtin_actions import _run_picked_interface_view

            return _run_picked_interface_view(
                session,
                targets["enzyme_spec"],
                targets["ligand_spec"],
                targets["cutoff"],
                executor=executor,
            )

        _run_toolbar_chimerax_task(session, "Interface", interface_task)
        return
    elif action == "ai-analysis-md":
        from .md import prompt_md_options, run_md

        default_spec = _prompt_toolbar_target_model_spec(session, "MD")
        if default_spec is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "MD: cancelled")
            return
        opts = prompt_md_options(session, default_target_spec=default_spec)
        if opts is None:
            _report_toolbar_result(session, "MD: cancelled")
            return

        def md_task(executor):
            return run_md(session, opts, executor=executor)

        _run_toolbar_chimerax_task(session, "MD", md_task)
        return
    elif action in {"ai-quick-triad-zoom", "ai-analysis-catalytic-zoom"}:
        target_model_hint = _prompt_toolbar_target_model_spec(session, "Zoom")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "Zoom: cancelled")
            return

        def catalytic_zoom_task(executor):
            from .builtin_actions import _run_catalytic_zoom

            return _run_catalytic_zoom(
                session,
                model_hint=target_model_hint,
                executor=executor,
                trans=80,
            )

        _run_toolbar_chimerax_task(session, "Zoom", catalytic_zoom_task)
        return
    elif action == "ai-analysis-membrane":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "Membrane")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "Membrane: cancelled")
            return

        def membrane_task(executor):
            from .membrane import run_membrane_view

            return run_membrane_view(session, model_hint=target_model_hint, executor=executor)

        _run_toolbar_chimerax_task(session, "Membrane", membrane_task)
        return
    elif action == "ai-analysis-pisa":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "PISA")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "PISA: cancelled")
            return

        def pisa_task(executor):
            from .pisa import run_pisa_view

            return run_pisa_view(
                session,
                f"view {target_model_hint}" if target_model_hint else "view",
                executor=executor,
            )

        _run_toolbar_chimerax_task(session, "PISA", pisa_task)
        return
    elif action == "ai-analysis-metal":
        options = _prompt_metal_analysis_options(session)
        if options is None:
            _report_toolbar_result(session, "Metal: cancelled")
            return

        def metal_task(executor):
            from .metal_placement import (
                format_metal_evidence_report,
                run_metal_placement_pipeline,
                run_metal_review_pipeline,
            )

            mode = options["mode"]
            if mode == "report":
                return format_metal_evidence_report(
                    session,
                    model_hint=options["model_hint"],
                    top_n=options["top_n"],
                    include_rcsb=options["include_rcsb"],
                    rows=options["rows"],
                )
            if mode == "quick":
                return run_metal_placement_pipeline(
                    session,
                    model_hint=options["model_hint"],
                    top_n=options["top_n"],
                    show_all=True,
                    clear_existing=True,
                    use_kvfinder=options["use_kvfinder"],
                    place=False,
                    preview=True,
                    draw_guides=True,
                    executor=executor,
                )

            review = run_metal_review_pipeline(
                session,
                model_hint=options["model_hint"],
                top_n=options["top_n"],
                include_rcsb=options["include_rcsb"],
                rows=options["rows"],
                min_tier=options["min_tier"],
                use_kvfinder=options["use_kvfinder"],
                clear_existing=True,
                preview=True,
                executor=executor,
            )
            if mode != "ask_place":
                return review

            candidates = list(getattr(session, "_codex_last_metal_candidates", []) or [])
            site_index = _call_ui_thread(
                session,
                lambda: _prompt_metal_candidate_choice(session, candidates),
            )
            if site_index is None:
                return review + "\n\nMetal insertion skipped; preview remains in the 3D view."
            placed = run_metal_placement_pipeline(
                session,
                model_hint=options["model_hint"],
                top_n=options["top_n"],
                show_all=False,
                clear_existing=True,
                use_kvfinder=options["use_kvfinder"],
                place=True,
                preview=False,
                site_index=site_index,
                draw_guides=True,
                executor=executor,
                candidates=candidates,
            )
            return review + "\n\n" + placed

        _run_toolbar_chimerax_task(session, "Metal", metal_task)
        return
    elif action == "ai-analysis-metal-report":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "Metal")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "Metal: cancelled")
            return

        def metal_report_task(executor):
            from .metal_placement import format_metal_evidence_report

            return format_metal_evidence_report(
                session,
                model_hint=target_model_hint,
                top_n=8,
                include_rcsb=True,
            )

        _run_toolbar_chimerax_task(session, "Metal", metal_report_task)
        return
    elif action == "ai-analysis-caver":
        from .caver import CodexCaverTool, caver_status

        tool, opened = _toggle_singleton_tool(session, CodexCaverTool, "CAVER")
        if opened and tool is not None:
            try:
                dock_widget = getattr(getattr(tool, "tool_window", None), "_dock_widget", None)
                if dock_widget is not None:
                    dock_widget.show()
                    dock_widget.raise_()
                    dock_widget.activateWindow()
                tool.refresh()
                tool._append(
                    "CAVER panel opened. 3D tunnel overlays appear after Run Local or Import Result succeeds.\n"
                    + caver_status(session)
                )
            except Exception:
                pass
            _report_toolbar_result(
                session,
                "CAVER panel opened. Use Run Local or Import Result to render tunnels in the current 3D view.",
            )
        return
    elif action == "ai-analysis-dali":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "DALI")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "DALI: cancelled")
            return
        _run_toolbar_chimerax_task(
            session,
            "DALI",
            lambda executor: launch_dali_server(session, executor=executor, model_hint=target_model_hint),
        )
        return
    elif action == "ai-analysis-vast":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "VAST")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "VAST: cancelled")
            return
        _run_toolbar_chimerax_task(
            session,
            "VAST",
            lambda executor: launch_vast_search(session, executor=executor, model_hint=target_model_hint),
        )
        return
    elif action == "ai-analysis-pdbefold":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "PDBeFold")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "PDBeFold: cancelled")
            return
        _run_toolbar_chimerax_task(
            session,
            "PDBeFold",
            lambda executor: launch_pdbefold_search(session, executor=executor, model_hint=target_model_hint),
        )
        return
    elif action == "ai-analysis-rmsd":
        opts = _prompt_rmsd_options(session)
        if opts is None:
            _report_toolbar_result(session, "RMSD: cancelled")
            return
        # MatchMaker touches the OpenGL graphics context (scene_position
        # updates, model re-display) which is bound to the main thread.
        # Running this through the background task runner causes
        # "OpenGL context current in wrong thread" errors -- compute
        # synchronously on the main thread instead. The work is fast
        # (sequence alignment + a few SVD fits per pair).
        from .rmsd_analysis import compute_rmsd_table, format_rmsd_table

        log = session.logger
        mode = "re-aligned" if opts["realign"] else "current coordinates"
        log.info(
            f"[RMSD] reference = {opts['ref_spec']}, "
            f"cutoff = {opts['cutoff']:.2f} Å, mode = {mode}"
        )
        try:
            rows = compute_rmsd_table(
                session,
                opts["ref_spec"],
                opts["target_specs"],
                realign=opts["realign"],
                cutoff=opts["cutoff"],
            )
        except Exception as err:
            _report_toolbar_result(session, f"RMSD failed: {err}", error=True)
            return
        log.info(format_rmsd_table(rows))
        _report_toolbar_result(session, f"RMSD: {len(rows)} chain pair(s) reported")
        return
    elif action == "ai-analysis-usalign":
        target_entries = _prompt_toolbar_alignment_entries(session, "US-align")
        if target_entries is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "US-align: cancelled")
            return
        _run_toolbar_chimerax_task(
            session,
            "US-align",
            lambda executor: launch_usalign(session, executor=executor, entries=target_entries),
        )
        return
    elif action == "ai-analysis-conserve":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "ConSurf")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "Consurf: cancelled")
            return
        # Web-first mode: open the ConSurf Colab notebook, paste FASTA, attempt
        # auto-fill. The MAFFT-based local analysis is still available via the
        # `/conservation` slash command in the AI Assistant.
        def consurf_task():
            return _launch_consurf_page(session, model_hint=target_model_hint)

        _run_toolbar_task(session, "Consurf web", consurf_task)
        return
    elif action == "ai-analysis-3dconserve":
        from chimerax.core.commands import run as _cx_run
        try:
            _cx_run(session, "ugradient")
            _report_toolbar_result(session, "3D Conserve: highlighted unique residues")
        except Exception as exc:
            _report_toolbar_result(
                session,
                f"3D Conserve failed: {exc}. Install the ChimeraX-3DConserve bundle if missing."
            )
        return
    elif action in {"ai-analysis-boltz", "ai-nucleotide-boltz"}:
        target_model_hint = _prompt_toolbar_target_model_spec(session, "Boltz")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "Boltz: cancelled")
            return
        _run_toolbar_task(session, "Boltz", lambda: launch_boltz_latest_predict(session, model_hint=target_model_hint))
        return
    elif action == "ai-analysis-rapidock":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "RAPiDock")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "RAPiDock: cancelled")
            return
        peptide = _prompt_peptide_sequence(session)
        if peptide is None:
            _report_toolbar_result(session, "RAPiDock: cancelled")
            return
        save_root = _prompt_rapidock_save_location(session, Path.home() / "Desktop" / "RAPiDock")
        if save_root is None:
            _report_toolbar_result(session, "RAPiDock: cancelled")
            return
        engine_choice = _prompt_rapidock_engine(session)
        if engine_choice is None:
            _report_toolbar_result(session, "RAPiDock: cancelled")
            return
        _run_toolbar_task(
            session,
            "RAPiDock",
            lambda: launch_rapidock_prediction(
                session,
                peptide=peptide,
                model_hint=target_model_hint,
                save_root=save_root,
                engine=engine_choice,
            ),
        )
        return
    elif action == "ai-analysis-hpepdock":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "HPEPDOCK")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "HPEPDOCK: cancelled")
            return
        peptide = _prompt_peptide_sequence(session)
        if peptide is None:
            _report_toolbar_result(session, "HPEPDOCK: cancelled")
            return
        save_root = _prompt_rapidock_save_location(session, Path.home() / "Desktop" / "RAPiDock")
        if save_root is None:
            _report_toolbar_result(session, "HPEPDOCK: cancelled")
            return
        _run_toolbar_task(
            session,
            "HPEPDOCK",
            lambda: launch_rapidock_prediction(
                session,
                peptide=peptide,
                model_hint=target_model_hint,
                save_root=save_root,
                engine="hpepdock",
            ),
        )
        return
    elif action in {"ai-analysis-foldmason", "ai-nucleotide-foldmason"}:
        target_model_hint = _prompt_toolbar_target_model_spec(session, "FoldMason")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "FoldMason: cancelled")
            return
        _run_toolbar_chimerax_task(
            session,
            "FoldMason",
            lambda executor: launch_foldmason(session, executor=executor, model_hint=target_model_hint),
        )
        return
    elif action == "ai-analysis-structalign":
        target_entries = _prompt_toolbar_alignment_entries(session, "StructMSA")
        if target_entries is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "StructMSA: cancelled")
            return
        try:
            from .structural_alignment_report import generate_structural_alignment_report

            message = generate_structural_alignment_report(
                session,
                entries=target_entries,
                mode="full",
                open_report=True,
            )
        except Exception as exc:
            _report_toolbar_result(session, f"StructMSA failed: {exc}", error=True)
            return
        _report_toolbar_result(session, message)
        return
    elif action in {"ai-analysis-folddisco", "ai-nucleotide-folddisco"}:
        _run_toolbar_chimerax_task(
            session,
            "FoldDisco",
            lambda executor: launch_folddisco(session, executor=executor),
        )
        return
    elif action == "ai-analysis-nucdock":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "NucDock")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "NucDock: canceled")
            return
        request_text = _prompt_nucleotide_sequence(session)
        if request_text is None:
            _report_toolbar_result(session, "NucDock: canceled")
            return
        _run_toolbar_chimerax_task(
            session,
            "NucDock",
            lambda executor: launch_nucleotide_docking_pipeline(
                session,
                request_text,
                executor=executor,
                model_hint=target_model_hint,
            ),
        )
        return
    elif action == "ai-analysis-afcomplex":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "AF Complex")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "AF Complex: canceled")
            return
        request_text = _prompt_nucleotide_sequence(session)
        if request_text is None:
            _report_toolbar_result(session, "AF Complex: canceled")
            return
        _run_toolbar_task(
            session,
            "AF Complex",
            lambda: launch_nucleotide_alphafold_pipeline(session, request_text, model_hint=target_model_hint),
        )
        return
    elif action == "ai-display-controls":
        from .display_controls import CodexDisplayControls

        _toggle_singleton_tool(session, CodexDisplayControls, "Display Controls")
        _tabify_helper_into_models_strip(session, raise_tool="display controls")
        return
    elif action == "ai-sequence-bar":
        from .sequence_bar import CodexSequenceBar

        _toggle_singleton_tool(session, CodexSequenceBar, "Sequence Bar")
        return
    elif action == "ai-action-pad":
        from .action_pad import CodexActionPad

        _toggle_singleton_tool(session, CodexActionPad, "Action Pad")
        _tabify_helper_into_models_strip(session, raise_tool="action pad")
        return
    elif action == "ai-camera-bookmarks":
        from chimerax.core.commands import run as _cx_run
        try:
            _cx_run(session, "ui tool show 'Camera Bookmarks'")
        except Exception as exc:
            _report_toolbar_result(session, f"Camera Bookmarks failed: {exc}")
            return
        _tabify_helper_into_models_strip(session, raise_tool="camera bookmarks")
        return
    elif action == "ai-assistant-open":
        try:
            from .tool import CodexAssistant

            assistant = CodexAssistant.get_singleton(session, create=True, display=True)
            if assistant is not None:
                try:
                    assistant.display(True)
                    assistant._show_assistant_tab()
                    assistant._focus_prompt()
                except Exception:
                    pass
        except Exception as exc:
            _report_toolbar_result(session, f"AI Assistant open failed: {exc}")
            return
        _tabify_helper_into_models_strip(session, raise_tool="ai assistant")
        return
    elif action == "ai-analysis-energy":
        # Score the currently selected atomic model with PyRosetta ref2015.
        # If the model has multiple chains, run InterfaceAnalyzer between the
        # smallest chain (peptide) and the rest (receptor).
        # Note: do NOT `from pathlib import Path` here -- Path is already
        # imported at module level and re-importing inside this function makes
        # it a local of run_toolbar_action(), which then breaks every other
        # elif branch that uses Path before this branch executes.
        import shutil, subprocess as _sp, tempfile, json
        log = session.logger
        if shutil.which("docker") is None:
            log.error("[Energy] Docker not on PATH.")
            _report_toolbar_result(session, "Energy: Docker not found.")
            return
        # Confirm PyRosetta is installed in the image
        try:
            r = _sp.run(
                ["docker", "run", "--rm", "--platform", "linux/amd64",
                 "--entrypoint", "/bin/bash",
                 "chimerax-codex-rapidock:cpu-amd64",
                 "-c", "python -c 'import pyrosetta'"],
                capture_output=True, timeout=60,
            )
            if r.returncode != 0:
                log.warning(
                    "[Energy] PyRosetta is not installed in the RAPiDock image. "
                    "Click the AI tab's PyRosetta button first (~15 min one-time install)."
                )
                _report_toolbar_result(session, "Energy: install PyRosetta first.")
                return
        except Exception as exc:
            log.error(f"[Energy] PyRosetta check failed: {exc}")
            return
        # Pick target model (use selected or prompt)
        target_spec = _prompt_toolbar_target_model_spec(session, "Energy")
        if target_spec is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "Energy: cancelled")
            return
        if not target_spec:
            _report_toolbar_result(session, "Energy: no model.")
            return

        def _energy_task():
            from chimerax.core.commands import run as _cx_run
            tmp = Path(tempfile.mkdtemp(prefix="codex_energy_"))
            pdb_in = tmp / "input.pdb"
            try:
                _cx_run(session, f"save {pdb_in} {target_spec} format pdb")
            except Exception as exc:
                return f"Energy: PDB export failed: {exc}"
            if not pdb_in.exists():
                return "Energy: PDB export silently failed."
            score_py = tmp / "score.py"
            score_py.write_text(_PYROSETTA_SCORE_SCRIPT, encoding="utf-8")
            cmd = [
                "docker", "run", "--rm",
                "--platform", "linux/amd64",
                "--entrypoint", "/bin/bash",
                "-v", f"{tmp}:/work",
                "chimerax-codex-rapidock:cpu-amd64",
                "-c", "python /work/score.py /work/input.pdb /work/result.json",
            ]
            try:
                proc = _sp.run(cmd, capture_output=True, text=True, timeout=900)
            except _sp.TimeoutExpired:
                return "Energy: scoring timed out (>15 min)."
            if proc.returncode != 0:
                stderr = (proc.stderr or "")[-1500:]
                return f"Energy: scoring failed.\n{stderr}"
            result_file = tmp / "result.json"
            if not result_file.exists():
                return f"Energy: no result.json. stdout: {(proc.stdout or '')[-1500:]}"
            try:
                results = json.loads(result_file.read_text())
            except Exception as exc:
                return f"Energy: parse failed: {exc}"
            log.info("=" * 50)
            log.info(f"[Energy] {target_spec} ref2015 scoring")
            log.info(f"  total_score (REU): {results.get('total_score', 'n/a')}")
            log.info(f"  per_residue_avg : {results.get('per_residue_avg', 'n/a')}")
            iface = results.get("interface")
            if iface:
                log.info(f"  Interface analysis (chain {iface.get('peptide_chain')} vs rest):")
                log.info(f"    dG_separated  : {iface.get('dG_separated', 'n/a')}")
                log.info(f"    dSASA         : {iface.get('dSASA', 'n/a')} A^2")
                log.info(f"    n_hbonds      : {iface.get('hbonds', 'n/a')}")
                log.info(f"    packstat      : {iface.get('packstat', 'n/a')}")
            else:
                log.info("  (single chain — no interface analysis)")
            # Save CSV next to source PDB if possible
            csv_text = "metric,value\n"
            csv_text += f"total_score_REU,{results.get('total_score','')}\n"
            csv_text += f"per_residue_avg,{results.get('per_residue_avg','')}\n"
            if iface:
                for k, v in iface.items():
                    csv_text += f"interface_{k},{v}\n"
            out_csv = Path.home() / "Desktop" / f"chimerax_ref2015_{target_spec.lstrip('#').replace('/','_')}.csv"
            try:
                out_csv.write_text(csv_text)
                log.info(f"  CSV: {out_csv}")
            except Exception:
                pass
            return (
                f"Energy: total={results.get('total_score','?'):>9} REU"
                + (f", dG_sep={iface.get('dG_separated','?')}" if iface else "")
                + f". CSV: {out_csv}"
            )

        _run_toolbar_task(session, "Energy (ref2015)", _energy_task)
        return
    elif action == "ai-setup-pyrosetta":
        # Install PyRosetta into the existing rapidock Docker image so the
        # RAPiDock pipeline can run with `--scoring_function ref2015 --fastrelax`.
        import shutil, subprocess as _sp
        log = session.logger
        if shutil.which("docker") is None:
            log.error("[PyRosetta] Docker is not on PATH. Install Docker Desktop first.")
            _report_toolbar_result(session, "PyRosetta: Docker not found.")
            return
        # check the rapidock image exists
        try:
            r = _sp.run(["docker", "image", "inspect", "chimerax-codex-rapidock:cpu-amd64"],
                        capture_output=True, timeout=10)
            if r.returncode != 0:
                log.error("[PyRosetta] rapidock Docker image not found. Click RAPiDock first to build it.")
                _report_toolbar_result(session, "PyRosetta: build RAPiDock image first.")
                return
        except Exception as exc:
            log.error(f"[PyRosetta] Docker check failed: {exc}")
            return
        # warn user about disk + run install in background thread
        log.info("[PyRosetta] Starting install (~5 GB download). Progress in Log...")
        _report_toolbar_result(session, "PyRosetta install: started (background, ~10–20 min).")

        def _install_thread():
            log.info("[PyRosetta] step 1/4 — launching helper container")
            tmp_name = "rapidock_pyrosetta_install"
            _sp.run(["docker", "rm", "-f", tmp_name], capture_output=True)
            r1 = _sp.run([
                "docker", "run", "-d", "--name", tmp_name,
                "--platform", "linux/amd64",
                "--entrypoint", "/bin/bash",
                "chimerax-codex-rapidock:cpu-amd64",
                "-c", "sleep 7200",
            ], capture_output=True, text=True)
            if r1.returncode != 0:
                log.error(f"[PyRosetta] helper start failed: {r1.stderr}")
                return
            log.info("[PyRosetta] step 2/4 — pip install pyrosetta-installer (small)")
            r2 = _sp.run(["docker", "exec", tmp_name,
                          "pip", "install", "pyrosetta-installer"],
                         capture_output=True, text=True, timeout=600)
            if r2.returncode != 0:
                log.error(f"[PyRosetta] installer pip failed: {r2.stderr[:500]}")
                _sp.run(["docker", "rm", "-f", tmp_name], capture_output=True)
                return
            log.info("[PyRosetta] step 3/4 — downloading + installing PyRosetta (~5 GB, slow)")
            r3 = _sp.run(["docker", "exec", tmp_name, "python", "-c",
                          "import pyrosetta_installer as p; p.install_pyrosetta()"],
                         capture_output=True, text=True, timeout=3600)
            if r3.returncode != 0:
                log.error(f"[PyRosetta] install failed: {r3.stderr[:1000]}")
                _sp.run(["docker", "rm", "-f", tmp_name], capture_output=True)
                return
            log.info("[PyRosetta] step 4/4 — committing image")
            r4 = _sp.run(["docker", "commit",
                          "-c", 'ENTRYPOINT ["python","/app/inference.py"]',
                          tmp_name, "chimerax-codex-rapidock:cpu-amd64"],
                         capture_output=True, text=True, timeout=300)
            _sp.run(["docker", "rm", "-f", tmp_name], capture_output=True)
            if r4.returncode != 0:
                log.error(f"[PyRosetta] commit failed: {r4.stderr}")
                return
            log.info(
                "[PyRosetta] DONE. Next RAPiDock click can pass "
                "--scoring_function ref2015 --fastrelax. "
                "Set os.environ['RAPIDOCK_USE_REF2015']='1' to enable by default."
            )
            try:
                import os as _os2
                _os2.environ["RAPIDOCK_USE_REF2015"] = "1"
            except Exception:
                pass
            _report_toolbar_result(session, "PyRosetta installed. ref2015 scoring enabled.")

        import threading
        threading.Thread(target=_install_thread, daemon=True).start()
        return
    elif action == "ai-analysis-hydrophobicity":
        from chimerax.core.commands import run as _cx_run
        from chimerax.atomic import AtomicStructure
        log = session.logger
        log.info("[MLP] starting hydrophobicity coloring...")
        visible_specs = [
            f"#{m.id_string}"
            for m in session.models.list()
            if isinstance(m, AtomicStructure) and getattr(m, "visible", True)
        ]
        if not visible_specs:
            log.warning("[MLP] No visible atomic structure to color.")
            _report_toolbar_result(session, "MLP: no visible atomic structure.")
            return
        log.info(f"[MLP] visible atomic structures: {visible_specs}")
        # Step 1: per-model surfaces (separate commands so a single failure
        # doesn't abort the whole batch). MLP is a standalone action -- it
        # does NOT coordinate with the Interface picker. Surface + stick
        # visibility / transparency / colour are now controlled by the
        # Display Ctrl panel so the user can mix them freely.
        for s in visible_specs:
            try:
                _cx_run(session, f"surface {s} & protein")
            except Exception as exc:
                log.warning(f"[MLP] surface for {s} failed: {exc}")
        # Step 2: MLP with fixed range. Issue per-model so partial failures
        # are visible.
        ok = 0
        for s in visible_specs:
            try:
                _cx_run(session, f"mlp {s} surfaces true range -20,20")
                ok += 1
            except Exception as exc:
                log.warning(f"[MLP] mlp for {s} failed: {exc}")
        # NOTE: we deliberately do NOT force a surface transparency here.
        # Surface translucency / colour / visibility is owned by the Display
        # Ctrl panel so the user controls it independently of MLP. Running
        # MLP just colours the surface; toggling visibility on/off and the
        # opacity slider live in the Display Ctrl tool.
        # Step 3: legend (key). Match the actual `lipophilicity` palette:
        #   darkcyan (0,139,139) -- white -- darkgoldenrod (184,134,11)
        try:
            _cx_run(
                session,
                "key darkcyan:Hydrophilic white:0 darkgoldenrod:Lipophilic "
                "pos 0.05,0.06 size 0.35,0.04 fontSize 14 "
                "colorTreatment blended ticks true labelColor black "
            )
        except Exception as exc:
            log.warning(f"[MLP] key (legend) failed: {exc}")
        log.info(f"[MLP] done. coloured {ok}/{len(visible_specs)} structures, "
                 f"range -20..+20.")
        _report_toolbar_result(
            session,
            f"MLP: {ok}/{len(visible_specs)} structures, range -20..+20."
        )
        return
    elif action == "ai-nucleotide-dock":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "NucDock")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "NucDock: canceled")
            return
        request_text = _prompt_nucleotide_sequence(session)
        if request_text is None:
            _report_toolbar_result(session, "NucDock: canceled")
            return
        _run_toolbar_chimerax_task(
            session,
            "NucDock",
            lambda executor: launch_nucleotide_docking_pipeline(
                session,
                request_text,
                executor=executor,
                model_hint=target_model_hint,
            ),
        )
        return
    elif action == "ai-nucleotide-afcomplex":
        target_model_hint = _prompt_toolbar_target_model_spec(session, "AF Complex")
        if target_model_hint is _TARGET_SELECTION_CANCELLED:
            _report_toolbar_result(session, "AF Complex: canceled")
            return
        request_text = _prompt_nucleotide_sequence(session)
        if request_text is None:
            _report_toolbar_result(session, "AF Complex: canceled")
            return
        _run_toolbar_task(
            session,
            "AF Complex",
            lambda: launch_nucleotide_alphafold_pipeline(session, request_text, model_hint=target_model_hint),
        )
        return
