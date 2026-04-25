#!/usr/bin/env python3
import importlib.util
import re
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
BUILTIN_ACTIONS = SRC_DIR / "builtin_actions.py"


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class DummySession:
    def __init__(self):
        self._codex_bridge_layout_spacing = 18.0
        self._codex_bridge_layout_reverse_commands = []
        self._codex_bridge_figure_history = []
        self._codex_bridge_last_figure_mode = ""
        self._codex_bridge_figure_cycle_index = -1
        self._codex_bridge_dali_history = []
        self._codex_bridge_semantics = {
            "models": [
                {
                    "spec": "#1",
                    "name": "test_model",
                    "atomic": True,
                    "chains": [
                        {"id": "A", "start": 1, "end": 300, "count": 300},
                        {"id": "B", "start": 1, "end": 240, "count": 240},
                    ],
                }
            ],
            "aliases": {"#1": "#1", "testmodel": "#1"},
            "selection": {"models": ["#1"], "atoms": 10, "residues": 5, "ranges": ["#1/A:100-120"]},
        }

        class _Selection:
            def models(self_inner):
                class _Model:
                    id_string = "1"
                return [_Model()]

            def clear(self_inner):
                return None

        self.selection = _Selection()

    def __getattr__(self, name):
        raise AttributeError(name)


def run_eval():
    chimerax = types.ModuleType("chimerax")
    core = types.ModuleType("chimerax.core")
    commands = types.ModuleType("chimerax.core.commands")
    atomic = types.ModuleType("chimerax.atomic")
    commands.ObjectsArg = type(
        "ObjectsArg",
        (),
        {"parse": staticmethod(lambda spec, session: (types.SimpleNamespace(atoms=[]), spec, ""))},
    )
    atomic.selected_atoms = lambda session: []
    atomic.selected_residues = lambda session: []
    sys.modules["chimerax"] = chimerax
    sys.modules["chimerax.core"] = core
    sys.modules["chimerax.core.commands"] = commands
    sys.modules["chimerax.atomic"] = atomic

    package = types.ModuleType("chimeraxtestpkg")
    package.__path__ = [str(SRC_DIR)]
    sys.modules["chimeraxtestpkg"] = package
    docs_index = types.ModuleType("chimeraxtestpkg.docs_index")
    docs_index.command_snippets_for_prompt = lambda prompt, limit=6: []
    docs_index.format_docs_snippets = lambda prompt, limit=6: ""
    docs_index.likely_command_aliases = lambda prompt, limit=8: []
    docs_index.docs_index_stats = lambda: {"count": 0, "sources": {}}
    sys.modules["chimeraxtestpkg.docs_index"] = docs_index

    semantic = types.ModuleType("chimeraxtestpkg.semantic")
    semantic.extract_residue_specs_from_text = lambda session, text, model_hint=None: _fake_residue_specs(text, model_hint=model_hint)
    semantic.extract_chain_specs_from_text = lambda session, text, model_hint=None: _fake_chain_specs(text, model_hint=model_hint)
    semantic.extract_model_specs_from_text = lambda session, text, limit=8: _fake_model_specs(text)[:limit]
    semantic.format_selection_focus_report = lambda session, model_hint=None: "selection-focus"
    semantic.format_selection_overlap_report = lambda session, model_hint=None: "selection-overlap"
    semantic.format_analyze_report = lambda session, model_hint=None: "analyze"
    semantic.format_annotation_report = lambda session, model_hint=None: "annotate"
    semantic.format_motif_report = lambda session, model_hint=None, motif_text=None: "motif"
    semantic.format_metal_report = lambda session, model_hint=None: "metal"
    semantic.format_ligand_report = lambda session, model_hint=None: "ligand"
    semantic.format_catalytic_report = lambda session, model_hint=None: "catalytic"
    semantic.format_catalytic_workflow_report = lambda session, model_hint=None: "catalytic-workflow"
    semantic.format_complex_report = lambda session, model_hint=None: "complex"
    semantic.format_roles_report = lambda session, model_hint=None: "roles"
    semantic.format_domains_report = lambda session, model_hint=None: "domains"
    semantic.format_sequence_report = lambda session, model_hint=None: "sequence"
    semantic.format_chains_report = lambda session, model_hint=None: "chains"
    semantic.format_uniprot_feature_report = lambda session, model_hint=None: "features"
    semantic.format_uniprot_motif_report = lambda session, query_text=None, model_hint=None, motif_text=None: "uniprot-motif"
    semantic.format_legend_report = lambda session, model_hint=None, style="text": "legend"
    semantic.format_caption_draft = lambda session, model_hint=None, style="paper": f"caption-{style}"
    semantic.format_panel_plan = lambda session, model_hint=None: "panels"
    semantic.format_figure_lab_report = lambda session, model_hint=None: "figure-lab"
    semantic.get_selection_overlap_payload = lambda session, model_hint=None: {
        "selection": session._codex_bridge_semantics["selection"],
        "catalytic_hits": [{"residue_spec": "#1/A:110", "name": "HIS", "score": 9, "consensus": "candidate"}],
        "motif_matches": [{"chain_id": "A", "start_number": 105, "end_number": 110, "pattern_name": "HExxH", "matched_sequence": "HEAAH", "residue_specs": ["#1/A:105", "#1/A:106", "#1/A:107", "#1/A:108", "#1/A:109", "#1/A:110"]}],
        "domain_matches": [{"spec": "#1/A:80-160", "display_label": "catalytic-core candidate"}],
        "ligand_matches": [{"ligand_label": "NAG A201", "ligand_chain": "A", "ligand_spec": "#1/A:201"}],
        "metal_matches": [{"metal_label": "ZN A301:ZN", "metal_chain": "A", "metal_spec": "#1/A:301", "site_residue_specs": ["#1/A:299", "#1/A:300"]}],
        "interface_matches": [{"chain_a": "A", "chain_b": "B", "chain_a_spec": "#1/A", "chain_b_spec": "#1/B", "min_distance": 3.2, "contacts_a": 18, "contacts_b": 17}],
    }
    semantic.get_domain_selections = lambda session, model_hint=None: []
    semantic.get_role_selections = lambda session, model_hint=None: []
    semantic.get_motif_hits = lambda session, model_hint=None, motif_text=None: []
    semantic.get_uniprot_feature_entries = lambda session, model_hint=None: []
    semantic.get_ligand_sites = lambda session, model_hint=None: []
    semantic.get_metal_sites = lambda session, model_hint=None: []
    semantic.best_catalytic_candidates = lambda session, model_hint=None, limit=12: []
    semantic.best_interface_pair = lambda session, model_hint=None: None
    semantic.best_ligand_site = lambda session, model_hint=None: None
    semantic.best_metal_site = lambda session, model_hint=None: None
    semantic.get_session_semantics = lambda session: session._codex_bridge_semantics
    semantic.match_models_from_text = lambda session, text, limit=2: []
    sys.modules["chimeraxtestpkg.semantic"] = semantic
    conservation = types.ModuleType("chimeraxtestpkg.conservation")
    conservation.format_conservation_report = lambda session, model_hint=None: "conservation"
    sys.modules["chimeraxtestpkg.conservation"] = conservation

    mod = _load_module(BUILTIN_ACTIONS, "chimeraxtestpkg.builtin_actions")
    session = DummySession()

    cases = [
        ("surface를 파란색 반투명으로 보여줘", "appearance"),
        ("체인별 색으로 바꿔줘", "appearance"),
        ("구조 예쁘게 정리해줘", "figure"),
        ("make the protein clean and pretty", "figure"),
        ("선택한 부분 motif랑 pocket 같이 보이게 해줘", "figure"),
        ("다음 figure 보여줘", "figure"),
        ("이전 figure로 돌아가", "figure"),
        ("A:100이랑 A:150 거리 재줘", "manipulation"),
        ("A:100 A:101 A:102 각도 재줘", "manipulation"),
        ("ligand랑 protein 사이 수소결합 보여줘", "manipulation"),
        ("contacts 보여주고 삭제해줘", "manipulation"),
        ("protein interface network 보여줘", "manipulation"),
        ("interface residues 선택해줘", "manipulation"),
        ("surface zone 해제해줘", "manipulation"),
        ("ligand랑 protein 사이 buried area 재줘", "manipulation"),
        ("surface area 재줘", "manipulation"),
        ("convexity로 보여줘", "manipulation"),
        ("녹화 시작해", "movie"),
        ("movie status", "movie"),
        ("movie encode /tmp/test.mp4", "movie"),
        ("legend save-table", "legend"),
        ("caption nature", "caption"),
        ("선택 기준으로 분석해줘", "report"),
        ("metal site 요약해줘", "report"),
        ("motif summary 보여줘", "report"),
        ("촉매 잔기 바로 찾아줘", "report"),
        ("catalytic residue triage 해줘", "report"),
        ("도메인 2 뜯어내서 dali search 해줘", "dali"),
    ]

    score = 0
    for text, expected in cases:
        action = mod._infer_clause_action(session, text)
        label = action["label"] if action else "none"
        ok = (
            (expected == "appearance" and label == "appearance")
            or (expected == "figure" and label.startswith("figure"))
            or (expected == "manipulation" and label == "manipulation")
            or (expected == "movie" and label == "movie")
            or (expected == "legend" and label.startswith("legend"))
            or (expected == "caption" and label.startswith("caption"))
            or (expected == "report" and label in {"selected analyze", "analyze", "metal", "motif", "ligand", "catalytic", "complex", "roles", "domains", "sequence", "chains", "selected", "annotate"})
            or (expected == "dali" and label.startswith("dali"))
        )
        score += 1 if ok else 0
        print(f"{'OK' if ok else 'FAIL'} | expected={expected:12s} got={label:20s} | {text}")

    percent = 100.0 * score / len(cases)
    print(f"\nscore={score}/{len(cases)} ({percent:.1f})")
    return 0 if percent >= 99.0 else 1

def _fake_model_specs(text):
    hits = re.findall(r"(#\d+(?:\.\d+)*)", str(text or ""))
    if hits:
        return hits
    return ["#1"]


def _fake_chain_specs(text, model_hint=None):
    model = model_hint or "#1"
    specs = []
    for match in re.finditer(r"(?:chain|체인)\s*([A-Za-z0-9])|([A-Za-z0-9])\s*(?:chain|체인)", str(text or ""), flags=re.IGNORECASE):
        chain_id = (match.group(1) or match.group(2) or "").upper()
        if chain_id:
            specs.append(f"{model}/{chain_id}")
    return specs


def _fake_residue_specs(text, model_hint=None):
    model = model_hint or "#1"
    specs = []
    source = str(text or "")
    for match in re.finditer(r"(#\d+(?:/[A-Za-z0-9?]+)?:(?:\d+(?:-\d+)?))", source):
        specs.append(match.group(1))
    for chain_id, start, end in re.findall(r"(?:chain\s+)?([A-Za-z0-9])\s*[: ]\s*(\d+)(?:\s*-\s*(\d+))?", source, flags=re.IGNORECASE):
        specs.append(f"{model}/{chain_id.upper()}:{start if not end else f'{start}-{end}'}")
    return specs


if __name__ == "__main__":
    raise SystemExit(run_eval())
