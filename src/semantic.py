import json
import re
from collections import Counter
from urllib.error import URLError
from urllib.request import Request, urlopen

import numpy as np

METAL_ELEMENTS = {
    "ZN", "MG", "MN", "FE", "CO", "NI", "CU", "CA", "NA", "K", "CD"
}
CATALYTIC_LIKE_RESNAMES = {
    "HIS", "HID", "HIE", "HIP", "HSD", "HSE", "HSP",
    "ASP", "GLU", "CYS", "SER", "THR", "TYR", "LYS", "ARG", "ASN", "GLN",
}
CATALYTIC_LIKE_ONE_LETTER = set("HDECSTYKRQN")
ONE_TO_THREE_RESNAME = {
    "A": "ALA",
    "C": "CYS",
    "D": "ASP",
    "E": "GLU",
    "F": "PHE",
    "G": "GLY",
    "H": "HIS",
    "I": "ILE",
    "K": "LYS",
    "L": "LEU",
    "M": "MET",
    "N": "ASN",
    "P": "PRO",
    "Q": "GLN",
    "R": "ARG",
    "S": "SER",
    "T": "THR",
    "V": "VAL",
    "W": "TRP",
    "Y": "TYR",
}
CATALYTIC_MOTIF_KEYWORDS = (
    "catalytic",
    "metal",
    "nuclease",
    "hydrolase",
    "kinase",
    "helicase",
    "redox",
    "cofactor",
    "zinc",
    "heme",
    "nucleotide",
)
DEFAULT_MOTIF_PATTERNS = (
    ("HExxH metalloprotease", r"HE..H", "metal/catalytic", "zinc metalloprotease-like catalytic signature", 1),
    ("HxxEH metalloprotease", r"H..EH", "metal/catalytic", "alternative gluzincin-like metal catalytic motif", 1),
    ("PD-(D/E)xK nuclease", r"P[DE][DE].K", "nuclease", "PD-(D/E)xK nuclease-like catalytic core", 1),
    ("HNH nuclease", r"HNH", "nuclease", "HNH nuclease catalytic signature", 1),
    ("Walker A P-loop", r"[AG].{4}GK[ST]", "nucleotide-binding", "P-loop NTP-binding phosphate-loop motif", 1),
    ("Walker B", r"[LIVMFYW]{4}DE", "nucleotide-binding", "Walker B Mg2+/ATPase acidic motif", 1),
    ("DEAD-box helicase", r"DEAD", "helicase", "DEAD-box helicase motif II", 1),
    ("DEAH-box helicase", r"DEAH", "helicase", "DEAH-box helicase motif II", 1),
    ("Rossmann GxGxxG", r"G.G..G", "nucleotide-binding", "Rossmann-like dinucleotide-binding glycine motif", 2),
    ("DxDxT/N metal-binding", r"D.D[STN]", "metal/catalytic", "acidic metal-binding phosphohydrolase-like motif", 2),
    ("DxD metal-binding", r"D.D", "metal/catalytic", "acidic metal-binding glycosyltransferase-like motif", 2),
    ("CxxCH heme-c", r"C..CH", "cofactor-binding", "c-type cytochrome heme attachment motif", 2),
    ("C2H2 zinc finger", r"C.{2,4}C.{3,16}H.{3,5}H", "zinc-binding", "Cys2His2 zinc-finger-like motif", 2),
    ("CxxC redox/metal", r"C..C", "redox/metal", "thioredoxin-like redox or metal-binding Cys pair", 3),
    ("CxxxC metal", r"C...C", "redox/metal", "Cys-rich metal or Fe-S coordination candidate", 3),
    ("GxSxG hydrolase", r"G.S.G", "hydrolase", "serine hydrolase/lipase-like nucleophile loop", 3),
    ("GDSG hydrolase", r"GDSG", "hydrolase", "esterase/lipase-like catalytic serine motif", 3),
    ("HRD kinase", r"HRD", "kinase", "protein kinase catalytic loop motif", 3),
    ("DFG kinase", r"DFG", "kinase", "protein kinase Mg2+-binding activation-loop motif", 3),
    ("RGD adhesion", r"RGD", "binding", "integrin-binding RGD motif", 4),
    ("N-glycosylation sequon", r"N[^P][ST]", "PTM", "N-linked glycosylation sequon", 4),
    ("DxH catalytic/metal", r"D.H", "metal/catalytic", "Asp-His catalytic or metal-coordination candidate", 5),
    ("HxH catalytic/metal", r"H.H", "metal/catalytic", "His-rich catalytic or metal-coordination candidate", 5),
)
DEFAULT_DOMAIN_MIN_LENGTH = 35
DEFAULT_DOMAIN_MAX_CHUNKS = 6

UNIPROT_FEATURE_TYPE_ALIASES = {
    "domain": "Domain",
    "region": "Region",
    "motif": "Motif",
    "repeat": "Repeat",
    "zinc finger": "Zinc finger",
    "coiled coil": "Coiled coil",
    "active site": "Active site",
    "binding site": "Binding site",
    "metal binding": "Metal binding",
    "site": "Site",
    "dna binding": "DNA binding",
    "nucleotide phosphate binding region": "Nucleotide-binding",
}
UNIPROT_FEATURE_PRIORITY = {
    "Active site": 0,
    "Metal binding": 1,
    "Binding site": 2,
    "Motif": 3,
    "Zinc finger": 4,
    "DNA binding": 5,
    "Nucleotide-binding": 6,
    "Domain": 7,
    "Region": 8,
    "Repeat": 9,
    "Coiled coil": 10,
    "Site": 11,
}


def initialize_semantic_cache(session):
    if hasattr(session, "_codex_bridge_semantic_handler"):
        return
    session._codex_bridge_semantics = None
    session._codex_bridge_semantic_handler = session.triggers.add_handler(
        "command finished",
        lambda *_args, ses=session: invalidate_semantic_cache(ses),
    )


def invalidate_semantic_cache(session):
    session._codex_bridge_semantics = None


def get_session_semantics(session):
    if getattr(session, "_codex_bridge_semantics", None) is None:
        session._codex_bridge_semantics = _collect_session_semantics(session)
    return session._codex_bridge_semantics


def summarize_semantics(semantics, *, include_models=True, include_selection=True):
    lines = []

    if include_models:
        lines.extend(_model_lines(semantics))
    if include_selection:
        lines.extend(_selection_lines(semantics))
    if not lines:
        lines.append("- No ChimeraX context attached.")
    return lines


def resolve_model_spec(session, text):
    semantics = get_session_semantics(session)
    normalized = _normalize(text)
    if not normalized:
        return None
    return semantics["aliases"].get(normalized)


def match_models_from_text(session, text, limit=2):
    semantics = get_session_semantics(session)
    normalized = _normalize(text)
    hits = []
    if normalized:
        for model in semantics["models"]:
            if model["id_alias"] in normalized or any(alias in normalized for alias in model["name_aliases"]):
                hits.append(model["spec"])

    deduped = []
    seen = set()
    for spec in hits:
        if spec not in seen:
            deduped.append(spec)
            seen.add(spec)
    if len(deduped) >= limit:
        return deduped[:limit]

    atomic_specs = [m["spec"] for m in semantics["models"] if m.get("atomic")]
    if len(atomic_specs) == limit:
        return atomic_specs
    return deduped


def extract_model_specs_from_text(session, text, limit=8):
    source = str(text or "").strip()
    if not source:
        return []

    explicit = re.findall(r"(#\d+(?:\.\d+)*)", source)
    matched = match_models_from_text(session, source, limit=limit)
    combined = []
    for spec in [*explicit, *matched]:
        if spec not in combined:
            combined.append(spec)
    return combined[:limit]


def resolve_default_model_spec(session):
    semantics = get_session_semantics(session)
    selected = semantics["selection"]["models"]
    if selected:
        return selected[0]
    atomic_specs = [model["spec"] for model in semantics["models"] if model.get("atomic")]
    if len(atomic_specs) == 1:
        return atomic_specs[0]
    return atomic_specs[0] if atomic_specs else None


def extract_chain_specs_from_text(session, text, model_hint=None):
    source = str(text or "").strip()
    if not source:
        return []

    resolved_model = resolve_model_spec(session, model_hint) if model_hint else None
    default_model = resolved_model or resolve_default_model_spec(session)
    semantics = get_session_semantics(session)
    model_entry = None
    if default_model:
        for item in semantics["models"]:
            if item["spec"] == default_model:
                model_entry = item
                break

    specs = []

    for match in re.finditer(r"(#\d+(?:\.\d+)*(?:/[A-Za-z0-9?]+))\b", source):
        specs.append(match.group(1))

    chain_letter_pattern = re.compile(r"(?:chain|체인)\s*([A-Za-z0-9])\b|([A-Za-z0-9])\s*(?:chain|체인)\b", re.IGNORECASE)
    for left, right in chain_letter_pattern.findall(source):
        chain_id = (left or right or "").upper()
        if not chain_id or not default_model:
            continue
        if model_entry and any(chain["id"] == chain_id for chain in model_entry["chains"]):
            specs.append(f"{default_model}/{chain_id}")

    deduped = []
    seen = set()
    for spec in specs:
        token = spec.strip()
        if token and token not in seen:
            deduped.append(token)
            seen.add(token)
    return deduped


def extract_residue_specs_from_text(session, text, model_hint=None):
    source = str(text or "").strip()
    if not source:
        return []

    resolved_model = resolve_model_spec(session, model_hint) if model_hint else None
    default_model = resolved_model or resolve_default_model_spec(session)
    semantics = get_session_semantics(session)
    model_entry = None
    if default_model:
        for item in semantics["models"]:
            if item["spec"] == default_model:
                model_entry = item
                break

    specs = []

    for match in re.finditer(r"(#\d+(?:/[A-Za-z0-9?]+)?:(?:\d+(?:-\d+)?))", source):
        specs.append(match.group(1))

    chain_range_pattern = re.compile(r"(?:chain\s+)?([A-Za-z0-9])\s*[: ]\s*(\d+)(?:\s*-\s*(\d+))?", re.IGNORECASE)
    for chain_id, start, end in chain_range_pattern.findall(source):
        if not default_model:
            continue
        residue_range = start if not end else f"{start}-{end}"
        specs.append(f"{default_model}/{chain_id.upper()}:{residue_range}")

    residue_name_pattern = re.compile(r"\b([A-Za-z]{3})\s*([A-Za-z0-9]?)\s*(\d+)\b")
    for _resname, chain_id, number in residue_name_pattern.findall(source):
        if not default_model:
            continue
        chain_token = chain_id.upper() if chain_id else None
        if chain_token is None and model_entry and len(model_entry["chains"]) == 1:
            chain_token = model_entry["chains"][0]["id"]
        if chain_token is None:
            continue
        specs.append(f"{default_model}/{chain_token}:{number}")

    deduped = []
    seen = set()
    for spec in specs:
        token = spec.strip()
        if token and token not in seen:
            deduped.append(token)
            seen.add(token)
    return deduped


def format_models_report(session):
    semantics = get_session_semantics(session)
    return "\n".join(_model_lines(semantics))


def format_annotation_report(session, model_hint=None):
    from chimerax.atomic import AtomicStructure
    from chimerax.atomic.structure import uniprot_ids

    selected_spec = resolve_model_spec(session, model_hint) if model_hint else None
    lines = []
    for model in session.models.list(type=AtomicStructure):
        spec = f"#{getattr(model, 'id_string', '?')}"
        if selected_spec and spec != selected_spec:
            continue

        lines.append(f"- {spec} {getattr(model, 'name', 'structure')}")
        useqs = uniprot_ids(model)
        if useqs:
            seen = set()
            for useq in useqs:
                key = (useq.chain_id, useq.uniprot_id, useq.uniprot_name)
                if key in seen:
                    continue
                seen.add(key)
                lines.append(f"  - UniProt: chain {useq.chain_id} -> {useq.uniprot_id} ({useq.uniprot_name})")
        else:
            lines.append("  - UniProt: none detected")

        ligand_sites = get_ligand_sites(session, model_hint=spec)
        metal_sites = get_metal_sites(session, model_hint=spec)
        catalytic = score_catalytic_residues(session, model_hint=spec)
        motifs = get_motif_hits(session, model_hint=spec)
        feature_entries = get_uniprot_feature_entries(session, model_hint=spec)

        if ligand_sites:
            lines.append("  - ligand annotations:")
            for site in ligand_sites[:4]:
                lines.append(f"    - {site['ligand_label']} on chain {site['ligand_chain']} (score {site['score']})")
        else:
            lines.append("  - ligand annotations: none detected")

        if metal_sites:
            lines.append("  - metal annotations:")
            for site in metal_sites[:4]:
                lines.append(f"    - {site['metal_label']} on chain {site['metal_chain']} (score {site['score']})")
        else:
            lines.append("  - metal annotations: none detected")

        if catalytic:
            lines.append("  - top catalytic candidates:")
            for candidate in catalytic[:6]:
                lines.append(
                    f"    - {candidate['residue_spec']} {candidate['name']} "
                    f"score {candidate['score']} [{candidate.get('consensus', 'candidate')}]"
                )
        else:
            lines.append("  - top catalytic candidates: none detected")
        if motifs:
            lines.append("  - motif hits:")
            for motif in motifs[:6]:
                lines.append(
                    f"    - {motif['chain_id']}:{motif['start_number']}-{motif['end_number']} "
                    f"{motif['pattern_name']} [{motif['matched_sequence']}]"
                )
        else:
            lines.append("  - motif hits: none detected")
        if feature_entries:
            lines.append("  - UniProt features:")
            for feature in feature_entries[:8]:
                lines.append(
                    f"    - chain {feature['chain_id']} {feature['start']}-{feature['end']}: "
                    f"{feature['feature_type']} [{feature['label']}]"
                )
        else:
            lines.append("  - UniProt features: none fetched or mapped")
        dali_text = format_dali_report(session)
        if "No DALI history yet." not in dali_text:
            lines.append("  - DALI context:")
            lines.extend(f"    {line}" for line in dali_text.splitlines()[:4])

    if not lines:
        if model_hint:
            return f"- No atomic model matched: {model_hint}"
        return "- No atomic models open."
    return "\n".join(lines)


def format_research_brief(session, model_hint=None):
    observations = []
    hypotheses = []
    next_checks = []

    analyze = format_analyze_report(session, model_hint)
    sequence = format_sequence_report(session, model_hint)
    domains = format_domains_report(session, model_hint)
    complex_report = format_complex_report(session, model_hint)
    roles = format_roles_report(session, model_hint)
    metal = format_metal_report(session, model_hint)
    ligand = format_ligand_report(session, model_hint)
    catalytic = format_catalytic_report(session, model_hint)
    motifs = format_motif_report(session, model_hint)
    feature_report = format_uniprot_feature_report(session, model_hint)
    selection = format_selection_overlap_report(session, model_hint)
    dali = format_dali_report(session)

    observations.extend(_top_lines(analyze, prefix="- ", limit=10))
    if "No ligand residues detected." not in ligand:
        observations.append("- Ligand-like residues are present and define a local pocket neighborhood.")
        observations.extend(_top_lines(ligand, prefix="  - ", limit=10))
    if "No metal ions detected" not in metal and "No metal-containing" not in metal:
        observations.append("- Metal-binding site(s) detected.")
        observations.extend(_top_lines(metal, prefix="  - ", limit=10))
    if "No chain-chain interfaces detected." not in complex_report and "No multi-chain complex" not in complex_report:
        observations.append("- Chain-chain contacts consistent with a multimeric complex are present.")
        observations.extend(_top_lines(complex_report, prefix="  - ", limit=8))
    if "No role-like chain assignments detected." not in roles:
        observations.append("- Chain roles can be partitioned into active-site-bearing versus structural/interface-supporting groups.")
        observations.extend(_top_lines(roles, prefix="  - ", limit=8))
    observations.extend(_chunk_observation_lines(domains))
    observations.extend(_sequence_observation_lines(sequence))
    if "No current ChimeraX selection." not in selection:
        observations.append("- Current user selection overlaps specific structural hypotheses.")
        observations.extend(_top_lines(selection, prefix="  - ", limit=8))
    if "No motif-like sequence patterns detected." not in motifs:
        observations.append("- Sequence motif-like patterns consistent with catalytic or metal-binding signatures were detected.")
        observations.extend(_top_lines(motifs, prefix="  - ", limit=6))
    if "No UniProt feature annotations" not in feature_report:
        observations.append("- UniProt feature annotations provide external evidence for domain/site interpretation.")
        observations.extend(_top_lines(feature_report, prefix="  - ", limit=8))
    if "No DALI history yet." not in dali:
        observations.append("- DALI structural-homology context has been recorded for this session.")
        observations.extend(_top_lines(dali, prefix="  - ", limit=4))

    hypotheses.extend(_metal_hypothesis_lines(metal))
    hypotheses.extend(_ligand_hypothesis_lines(ligand))
    hypotheses.extend(_complex_hypothesis_lines(complex_report))
    hypotheses.extend(_role_hypothesis_lines(roles))
    hypotheses.extend(_domain_hypothesis_lines(domains))
    hypotheses.extend(_motif_hypothesis_lines(motifs))
    if "Active site" in feature_report or "Metal binding" in feature_report or "Binding site" in feature_report:
        hypotheses.append("- UniProt-curated functional features may support prioritizing specific motif or pocket residues.")
    if "summary:" in dali or "result_url:" in dali:
        hypotheses.append("- Structural homology from DALI may help prioritize domain/function interpretations.")
    if "score" in catalytic:
        hypotheses.append("- Residues that score in both metal and ligand neighborhoods are the strongest catalytic candidates.")
    if not hypotheses:
        hypotheses.append("- No strong catalytic or assembly hypothesis is available yet from local structural evidence alone.")

    next_checks.extend(_metal_next_checks(metal))
    next_checks.extend(_ligand_next_checks(ligand))
    next_checks.extend(_complex_next_checks(complex_report))
    next_checks.extend(_role_next_checks(roles))
    next_checks.extend(_domain_next_checks(domains))
    next_checks.extend(_motif_next_checks(motifs))
    if "No UniProt feature annotations" not in feature_report:
        next_checks.append("- Compare UniProt domain/site annotations against local domain chunks and catalytic candidates.")
    if "result_url:" in dali:
        next_checks.append("- Compare top DALI hits against current domain boundaries and active-site hypotheses.")
    if "score" in catalytic:
        next_checks.append("- Highlight top catalytic candidates and test whether they cluster in one pocket.")
    if not next_checks:
        next_checks.append("- Use `sequence`, `domains`, `complex`, and `metal` to collect more evidence before assigning function.")

    sections = [
        "Observations",
        *observations[:18],
        "",
        "Hypotheses",
        *hypotheses[:12],
        "",
        "Next checks",
        *next_checks[:12],
    ]
    return "\n".join(sections)


def format_ligand_report(session, model_hint=None, shell_cutoff=4.5):
    sites = get_ligand_sites(session, model_hint=model_hint, shell_cutoff=shell_cutoff)
    if not sites:
        if model_hint:
            return f"- No ligand-bearing atomic model matched: {model_hint}"
        return "- No ligand residues detected."

    lines = []
    for site in sites:
        lines.append(f"- {site['model_spec']} {site['model_name']}")
        lines.append(f"  - ligand: {site['ligand_label']} (score {site['score']})")
        if site["nearby"]:
            lines.append("    - nearby residues: " + ", ".join(_format_residue_hit(hit) for hit in site["nearby"][:10]))
        else:
            lines.append("    - nearby residues: none within %.1f A" % shell_cutoff)
        if site["catalytic_like"]:
            lines.append("    - catalytic-like nearby: " + ", ".join(_format_residue_hit(hit) for hit in site["catalytic_like"][:8]))
        else:
            lines.append("    - catalytic-like nearby: none within %.1f A" % shell_cutoff)
    return "\n".join(lines)


def format_motif_report(session, model_hint=None, motif_text=None):
    hits = get_motif_hits(session, model_hint=model_hint, motif_text=motif_text)
    if not hits:
        if model_hint:
            return f"- No motif-like sequence patterns detected for: {model_hint}"
        return "- No motif-like sequence patterns detected."

    lines = []
    category_counts = Counter(hit.get("category", "motif") for hit in hits)
    lines.append(
        "- Motif scan: "
        + ", ".join(f"{category} {count}" for category, count in category_counts.most_common())
    )
    current_model = None
    display_hits = sorted(hits, key=lambda item: (item.get("priority", 99), item["model_spec"], item["chain_id"], item["start_number"], item["pattern_name"]))
    for hit in display_hits[:32]:
        if current_model != hit["model_spec"]:
            current_model = hit["model_spec"]
            lines.append(f"- {hit['model_spec']} {hit['model_name']}")
        detail = f"; {hit['description']}" if hit.get("description") else ""
        lines.append(
            f"  - chain {hit['chain_id']} {hit['start_number']}-{hit['end_number']}: "
            f"{hit['pattern_name']} [{hit['matched_sequence']}] "
            f"({hit.get('category', 'motif')}, priority {hit.get('priority', '?')}){detail} -> {hit['selection_name']}"
        )
    if len(hits) > 32:
        lines.append(f"  - ... {len(hits) - 32} lower-priority motif hits omitted")
    return "\n".join(lines)


def format_uniprot_feature_report(session, model_hint=None, overlap_only=False, selection_fallback=False):
    entries = get_uniprot_feature_entries(
        session,
        model_hint=model_hint,
        overlap_only=overlap_only,
        selection_fallback=selection_fallback,
    )
    if not entries:
        if overlap_only:
            if model_hint:
                return f"- No UniProt feature annotations overlap the requested residue focus: {model_hint}"
            if selection_fallback:
                return "- No UniProt feature annotations overlap the current residue selection."
        if model_hint:
            return f"- No UniProt feature annotations fetched for: {model_hint}"
        return "- No UniProt feature annotations fetched or mapped."

    lines = []
    current_model = None
    current_chain = None
    for entry in entries[:32]:
        if current_model != entry["model_spec"]:
            current_model = entry["model_spec"]
            current_chain = None
            lines.append(f"- {entry['model_spec']} {entry['model_name']}")
        if current_chain != (entry["chain_id"], entry["accession"]):
            current_chain = (entry["chain_id"], entry["accession"])
            lines.append(f"  - chain {entry['chain_id']} -> {entry['accession']} ({entry['entry_name']})")
        lines.append(
            f"    - {entry['start']}-{entry['end']}: {entry['feature_type']} "
            f"[{entry['label']}] -> {entry['selection_name']}"
        )
    return "\n".join(lines)


def format_uniprot_residue_report(session, query_text=None):
    report = format_uniprot_feature_report(
        session,
        model_hint=query_text,
        overlap_only=True,
        selection_fallback=True,
    )
    if report.startswith("- No UniProt feature annotations overlap"):
        return "\n".join(
            [
                "- UniProt residue-focused feature lookup",
                "  - result: not found",
                "  - detail: " + report.lstrip("- ").strip(),
            ]
        )
    if report == "- No UniProt feature annotations overlap the current residue selection.":
        return "\n".join(
            [
                "- UniProt residue-focused feature lookup",
                "  - result: not found",
                "  - detail: no UniProt-annotated feature overlapped the current residue selection.",
            ]
        )
    return "\n".join(
        [
            "- UniProt residue-focused feature lookup",
            "  - result: found",
            report,
        ]
    )


def format_uniprot_motif_report(session, query_text=None, model_hint=None, motif_text=None):
    resolved_hint = model_hint or query_text
    motif_hits = get_motif_hits(session, model_hint=resolved_hint, motif_text=motif_text)
    feature_entries = get_uniprot_feature_entries(
        session,
        model_hint=resolved_hint,
        overlap_only=False,
        selection_fallback=True,
    )

    if not motif_hits and not feature_entries:
        return "\n".join(
            [
                "- UniProt sequence-motif analysis",
                "  - result: not found",
                "  - detail: no motif-like hits or mapped UniProt feature annotations were found for the requested target sequence.",
            ]
        )

    overlaps = _motif_feature_overlaps(motif_hits, feature_entries)
    lines = [
        "- UniProt sequence-motif analysis",
        "  - result: found",
    ]

    accession_bits = []
    seen_accessions = set()
    for entry in feature_entries:
        key = (entry["chain_id"], entry["accession"])
        if key in seen_accessions:
            continue
        seen_accessions.add(key)
        accession_bits.append(f"chain {entry['chain_id']} -> {entry['accession']} ({entry['entry_name']})")
    if accession_bits:
        lines.append("  - UniProt targets:")
        lines.extend(f"    - {bit}" for bit in accession_bits[:8])

    if motif_hits:
        lines.append("  - motif-like sequence hits:")
        for hit in motif_hits[:12]:
            lines.append(
                f"    - {hit['model_spec']}/{hit['chain_id']} {hit['start_number']}-{hit['end_number']}: "
                f"{hit['pattern_name']} [{hit['matched_sequence']}]"
            )
    else:
        lines.append("  - motif-like sequence hits: none detected")

    if feature_entries:
        lines.append("  - mapped UniProt features:")
        for entry in feature_entries[:12]:
            lines.append(
                f"    - chain {entry['chain_id']} {entry['start']}-{entry['end']}: "
                f"{entry['feature_type']} [{entry['label']}]"
            )
    else:
        lines.append("  - mapped UniProt features: none fetched or mapped")

    if overlaps:
        lines.append("  - motif/feature overlaps:")
        for item in overlaps[:12]:
            lines.append(
                f"    - chain {item['chain_id']} motif {item['motif_label']} overlaps "
                f"{item['feature_type']} [{item['feature_label']}] at {item['overlap_text']}"
            )
    else:
        lines.append("  - motif/feature overlaps: none detected")

    return "\n".join(lines)


def get_uniprot_feature_entries(session, model_hint=None, overlap_only=False, selection_fallback=False):
    from chimerax.atomic import AtomicStructure
    from chimerax.atomic.structure import uniprot_ids

    target = _resolve_uniprot_feature_target(
        session,
        model_hint=model_hint,
        selection_fallback=selection_fallback,
    )
    selected_spec = target["model_spec"]
    selected_chains = set(target["chain_ids"])
    selected_ranges = target["ranges"]
    entries = []
    for model in session.models.list(type=AtomicStructure):
        model_spec = f"#{getattr(model, 'id_string', '?')}"
        if selected_spec and model_spec != selected_spec:
            continue
        for useq in uniprot_ids(model):
            accession = getattr(useq, "uniprot_id", None)
            chain_id = getattr(useq, "chain_id", None)
            if not accession or not chain_id:
                continue
            if selected_chains and chain_id not in selected_chains:
                continue
            payload = _fetch_uniprot_feature_payload(session, accession)
            if not payload:
                continue
            db_range = getattr(useq, "database_sequence_range", None)
            chain_range = getattr(useq, "chain_sequence_range", None)
            if not db_range or not chain_range:
                continue
            db_start, db_end = db_range
            chain_start, chain_end = chain_range
            model_token = _safe_selection_token(model_spec.lstrip("#"))
            chain_token = _safe_selection_token(chain_id)
            feature_index = 0
            for feature in _iter_uniprot_features(payload):
                mapped = _map_uniprot_feature_to_chain(feature, db_start, db_end, chain_start, chain_end)
                if mapped is None:
                    continue
                feature_index += 1
                start, end = mapped
                if overlap_only and not _range_overlaps_target(model_spec, chain_id, start, end, selected_ranges):
                    continue
                label = feature.get("label") or feature["feature_type"]
                feature_token = _slug_group_label(label)[:24] or "feature"
                entries.append(
                    {
                        "model_spec": model_spec,
                        "model_name": getattr(model, "name", "structure"),
                        "chain_id": chain_id,
                        "accession": accession,
                        "entry_name": payload.get("uniProtkbId", accession),
                        "feature_type": feature["feature_type"],
                        "label": label,
                        "description": feature.get("description", ""),
                        "start": start,
                        "end": end,
                        "spec": f"{model_spec}/{chain_id}:{start}" if start == end else f"{model_spec}/{chain_id}:{start}-{end}",
                        "selection_name": f"feature_{model_token}_{chain_token}_{feature_token}_{feature_index}",
                        "group_name": f"group_{model_token}_feature_{chain_token}_{feature_token}_{feature_index}",
                    }
                )
    entries.sort(
        key=lambda item: (
            item["model_spec"],
            item["chain_id"],
            UNIPROT_FEATURE_PRIORITY.get(item["feature_type"], 99),
            item["start"],
            item["end"],
            item["label"].lower(),
        )
    )
    return entries


def _resolve_uniprot_feature_target(session, model_hint=None, selection_fallback=False):
    source = str(model_hint or "").strip()
    model_spec = resolve_model_spec(session, source) if source else None
    chain_ids = []
    ranges = []

    chain_specs = extract_chain_specs_from_text(session, source, model_hint=model_spec) if source else []
    residue_specs = extract_residue_specs_from_text(session, source, model_hint=model_spec) if source else []
    inferred_specs = [*chain_specs, *residue_specs]
    if inferred_specs and not model_spec:
        model_spec = inferred_specs[0].split("/", 1)[0]

    for spec in inferred_specs:
        parsed = _parse_residue_focus_spec(spec)
        if parsed is None:
            continue
        spec_model, chain_id, start, end = parsed
        if model_spec is None:
            model_spec = spec_model
        if chain_id and chain_id not in chain_ids:
            chain_ids.append(chain_id)
        if start is not None:
            ranges.append((spec_model, chain_id, start, end))

    if selection_fallback and not inferred_specs:
        selection = get_session_semantics(session).get("selection", {})
        selection_ranges = list(selection.get("ranges", []))
        if selection_ranges:
            model_spec = selection_ranges[0].split("/", 1)[0]
            for spec in selection_ranges:
                parsed = _parse_residue_focus_spec(spec)
                if parsed is None:
                    continue
                spec_model, chain_id, start, end = parsed
                if spec_model != model_spec:
                    continue
                if chain_id and chain_id not in chain_ids:
                    chain_ids.append(chain_id)
                if start is not None:
                    ranges.append((spec_model, chain_id, start, end))

    return {
        "model_spec": model_spec,
        "chain_ids": tuple(chain_ids),
        "ranges": tuple(ranges),
    }


def _parse_residue_focus_spec(spec):
    text = str(spec or "").strip()
    match = re.match(r"(#\d+(?:\.\d+)*)/([A-Za-z0-9?]+)(?::(\d+)(?:-(\d+))?)?$", text)
    if not match:
        return None
    model_spec = match.group(1)
    chain_id = match.group(2)
    start = int(match.group(3)) if match.group(3) else None
    end = int(match.group(4)) if match.group(4) else start
    return model_spec, chain_id, start, end


def _range_overlaps_target(model_spec, chain_id, start, end, target_ranges):
    if not target_ranges:
        return True
    for target_model, target_chain, target_start, target_end in target_ranges:
        if target_model != model_spec or target_chain != chain_id:
            continue
        if target_start is None or target_end is None:
            return True
        if not (end < target_start or start > target_end):
            return True
    return False


def get_selection_overlap_payload(session, model_hint=None):
    from chimerax.atomic import AtomicStructure

    semantics = get_session_semantics(session)
    selection = semantics["selection"]
    if not selection["models"] and not selection["ranges"]:
        return None

    selected_specs = set(selection["ranges"])
    catalytic = score_catalytic_residues(session, model_hint=model_hint)
    motif_hits = get_motif_hits(session, model_hint=model_hint)
    domain_entries = [entry for entry in get_domain_selections(session, model_hint=model_hint) if entry["kind"] == "domain"]
    ligand_hits = get_ligand_sites(session, model_hint=model_hint)
    metal_hits = get_metal_sites(session, model_hint=model_hint)
    feature_matches = get_uniprot_feature_entries(session, model_hint=model_hint, overlap_only=True, selection_fallback=True)
    interface_matches = []

    selected_chain_tokens = {_chain_token_from_spec(spec) for spec in selected_specs}
    selected_chain_tokens |= {_chain_token_from_spec(spec) for spec in selection["models"]}
    catalytic_hits = [entry for entry in catalytic if _spec_matches_selection(entry["residue_spec"], selected_specs, selected_chain_tokens)]
    motif_matches = [entry for entry in motif_hits if any(_spec_matches_selection(spec, selected_specs, selected_chain_tokens) for spec in entry["residue_specs"])]
    domain_matches = [entry for entry in domain_entries if _spec_matches_selection(entry["spec"], selected_specs, selected_chain_tokens)]
    ligand_matches = [
        entry for entry in ligand_hits
        if _spec_matches_selection(entry["ligand_spec"], selected_specs, selected_chain_tokens)
        or any(_spec_matches_selection(f"{entry['model_spec']}/{hit['chain_id']}:{int(hit['number'])}", selected_specs, selected_chain_tokens) for hit in entry["nearby"])
    ]
    metal_matches = [
        entry for entry in metal_hits
        if _spec_matches_selection(entry["metal_spec"], selected_specs, selected_chain_tokens)
        or any(_spec_matches_selection(spec, selected_specs, selected_chain_tokens) for spec in entry["site_residue_specs"])
    ]

    selected_spec = resolve_model_spec(session, model_hint) if model_hint else None
    for model in session.models.list(type=AtomicStructure):
        model_spec = f"#{getattr(model, 'id_string', '?')}"
        if selected_spec and model_spec != selected_spec:
            continue
        for item in get_complex_interfaces(model):
            if (
                _spec_matches_selection(item["chain_a_spec"], selected_specs, selected_chain_tokens)
                or _spec_matches_selection(item["chain_b_spec"], selected_specs, selected_chain_tokens)
            ):
                interface_matches.append(item)

    return {
        "selection": selection,
        "selected_specs": selected_specs,
        "selected_chain_tokens": selected_chain_tokens,
        "catalytic_hits": catalytic_hits,
        "motif_matches": motif_matches,
        "domain_matches": domain_matches,
        "ligand_matches": ligand_matches,
        "metal_matches": metal_matches,
        "feature_matches": feature_matches,
        "interface_matches": interface_matches,
    }


def format_selection_overlap_report(session, model_hint=None):
    payload = get_selection_overlap_payload(session, model_hint=model_hint)
    if payload is None:
        return "- No current ChimeraX selection."

    selection = payload["selection"]
    lines = [
        "- Selected models: " + (", ".join(selection["models"]) if selection["models"] else "(none)"),
    ]
    if selection["ranges"]:
        lines.append("- Selected ranges: " + ", ".join(selection["ranges"][:10]))

    if payload["catalytic_hits"]:
        lines.append("- Selected region overlaps catalytic candidates:")
        for entry in payload["catalytic_hits"][:6]:
            lines.append(f"  - {entry['residue_spec']} score {entry['score']} [{entry.get('consensus', 'candidate')}]")
    if payload["motif_matches"]:
        lines.append("- Selected region overlaps motif hits:")
        for entry in payload["motif_matches"][:6]:
            lines.append(f"  - {entry['chain_id']}:{entry['start_number']}-{entry['end_number']} {entry['pattern_name']} [{entry['matched_sequence']}]")
    if payload["domain_matches"]:
        lines.append("- Selected region overlaps domain-like chunks:")
        for entry in payload["domain_matches"][:6]:
            label = entry.get("display_label")
            suffix = f" [{label}]" if label else ""
            lines.append(f"  - {entry['spec']}{suffix}")
    if payload["ligand_matches"]:
        lines.append("- Selected region overlaps ligand-pocket neighborhoods:")
        for entry in payload["ligand_matches"][:4]:
            lines.append(f"  - {entry['ligand_label']} on chain {entry['ligand_chain']}")
    if payload["metal_matches"]:
        lines.append("- Selected region overlaps metal-centered sites:")
        for entry in payload["metal_matches"][:4]:
            lines.append(f"  - {entry['metal_label']} on chain {entry['metal_chain']}")
    if payload["feature_matches"]:
        lines.append("- Selected region overlaps UniProt features:")
        for entry in payload["feature_matches"][:6]:
            lines.append(
                f"  - chain {entry['chain_id']} {entry['start']}-{entry['end']}: "
                f"{entry['feature_type']} [{entry['label']}]"
            )
    if payload["interface_matches"]:
        lines.append("- Selected region overlaps chain-chain interfaces:")
        for entry in payload["interface_matches"][:4]:
            lines.append(
                f"  - interface {entry['chain_a']}-{entry['chain_b']} "
                f"(min {entry['min_distance']:.2f} A)"
            )

    if len(lines) == 1 and selection["ranges"]:
        lines.append("- Selection currently does not overlap motif/catalytic/domain heuristics.")
    return "\n".join(lines)


def format_selection_focus_report(session, model_hint=None):
    payload = get_selection_overlap_payload(session, model_hint=model_hint)
    if payload is None:
        return "- No current ChimeraX selection."

    lines = ["Selection-focused analysis", *format_selection_overlap_report(session, model_hint=model_hint).splitlines()]
    if payload["catalytic_hits"]:
        lines.append("- Selected catalytic ranking")
        for entry in payload["catalytic_hits"][:8]:
            lines.append(
                f"  - {entry['residue_spec']} {entry['name']} score {entry['score']} "
                f"[{entry.get('consensus', 'candidate')}]"
            )
    if payload["motif_matches"]:
        lines.append("- Selected motif ranking")
        for entry in payload["motif_matches"][:6]:
            lines.append(
                f"  - {entry['chain_id']}:{entry['start_number']}-{entry['end_number']} "
                f"{entry['pattern_name']} [{entry['matched_sequence']}]"
            )
    if payload["feature_matches"]:
        lines.append("- Selected UniProt feature overlap")
        for entry in payload["feature_matches"][:6]:
            lines.append(
                f"  - {entry['chain_id']}:{entry['start']}-{entry['end']} "
                f"{entry['feature_type']} [{entry['label']}]"
            )
    if payload["interface_matches"]:
        lines.append("- Selected interface ranking")
        for entry in payload["interface_matches"][:4]:
            lines.append(
                f"  - {entry['chain_a']}-{entry['chain_b']} "
                f"{entry['contacts_a']}+{entry['contacts_b']} residues"
            )
    return "\n".join(lines)


def format_caption_draft(session, model_hint=None, style="paper"):
    semantics = get_session_semantics(session)
    selected_spec = resolve_model_spec(session, model_hint) if model_hint else None
    models = [model for model in semantics["models"] if model.get("atomic") and (not selected_spec or model["spec"] == selected_spec)]
    if not models:
        return "- No atomic models open."

    model = models[0]
    domains = [entry for entry in get_domain_selections(session, model_hint=model["spec"]) if entry["kind"] == "domain"]
    roles = get_role_selections(session, model_hint=model["spec"])
    motifs = get_motif_hits(session, model_hint=model["spec"])
    catalytic = best_catalytic_candidates(session, model_hint=model["spec"], limit=5)
    ligands = get_ligand_sites(session, model_hint=model["spec"])
    metals = get_metal_sites(session, model_hint=model["spec"])
    dali_text = format_dali_report(session)
    interfaces = []
    for chain_a in roles:
        if chain_a.get("role") == "active":
            interfaces = roles
            break

    selection_text = format_selection_overlap_report(session, model["spec"])
    has_selection = selection_text != "- No current ChimeraX selection."

    sentences = []
    if style == "short":
        sentences.append(f"{model['name']} shown as a domain-colored cartoon representation.")
    elif style == "nature":
        sentences.append(f"Overall architecture of {model['name']} shown as a cartoon representation with structurally distinct segments colored separately.")
    elif style == "panels":
        sentences.append(f"Panel A, overall architecture of {model['name']} displayed as a cartoon with structurally distinct segments.")
    elif style == "domain":
        sentences.append(f"Domain architecture of {model['name']} rendered as a cartoon with individually colored domain-like segments.")
    elif style == "pocket":
        sentences.append(f"Putative active-site region in {model['name']} shown with the surrounding scaffold in cartoon representation.")
    elif style == "interface":
        sentences.append(f"Inter-chain interface in {model['name']} highlighted within the multimeric assembly.")
    elif style == "selection":
        sentences.append(f"Selected structural region in {model['name']} highlighted on a domain-colored cartoon scaffold.")
    else:
        sentences.append(f"Figure draft: {model['name']} ({model['spec']}) displayed as a cartoon with distinct domain-like chunks.")
    if domains:
        labels = [entry.get("display_label") for entry in domains[:4] if entry.get("display_label")]
        if labels:
            prefix = "Panel B, " if style == "panels" else ""
            sentences.append(prefix + "domain-like regions are colored separately, highlighting " + ", ".join(labels) + ".")
    if roles and style in {"paper", "nature", "interface", "domain", "selection"}:
        active_roles = [entry for entry in roles if entry["role"] == "active"]
        scaffold_roles = [entry for entry in roles if entry["role"] == "scaffold"]
        if active_roles or scaffold_roles:
            role_bits = []
            if active_roles:
                role_bits.append("active-site-bearing chains")
            if scaffold_roles:
                role_bits.append("scaffold/interface-supporting chains")
            prefix = "Panel C, " if style == "panels" else ""
            sentences.append(prefix + "chain groups are separated to distinguish " + " and ".join(role_bits) + ".")
    if catalytic and style in {"paper", "nature", "pocket", "selection", "short"}:
        residues = ", ".join(f"{entry['name']} {entry['chain_id']}{entry['number']}" for entry in catalytic[:3])
        prefix = "Panel D, " if style == "panels" else ""
        sentences.append(prefix + f"top catalytic candidates ({residues}) are highlighted near the putative active-site region.")
    if ligands and style in {"paper", "nature", "pocket", "selection"}:
        prefix = "Panel E, " if style == "panels" else ""
        sentences.append(prefix + f"ligand pocket context is marked around {ligands[0]['ligand_label']}.")
    if metals and style in {"paper", "nature", "pocket", "selection"}:
        prefix = "Panel F, " if style == "panels" else ""
        sentences.append(prefix + f"metal coordination is emphasized around {metals[0]['metal_label']}.")
    if motifs and style in {"paper", "nature", "pocket", "selection", "domain"}:
        motif = motifs[0]
        prefix = "Panel G, " if style == "panels" else ""
        sentences.append(
            prefix + f"a sequence motif-like feature ({motif['pattern_name']}, {motif['matched_sequence']}) is mapped onto the same structural neighborhood."
        )
    if has_selection and style in {"selection", "paper", "nature", "pocket"}:
        selection_lines = [line.strip("- ").strip() for line in selection_text.splitlines() if line.startswith("- Selected region overlaps")]
        if selection_lines:
            prefix = "Panel H, " if style == "panels" else ""
            sentences.append(prefix + selection_lines[0] + ".")
    if style in {"paper", "nature", "domain"}:
        dali_lines = [line.strip("- ").strip() for line in dali_text.splitlines() if "top_hit:" in line or "fold:" in line or "summary:" in line]
        if dali_lines:
            summary_parts = []
            for line in dali_lines[:3]:
                if ":" in line:
                    summary_parts.append(line.split(":", 1)[1].strip())
            if summary_parts:
                sentences.append("DALI comparison supports a structurally related fold context: " + "; ".join(summary_parts) + ".")

    if style == "short":
        return "\n".join(f"- {sentence}" for sentence in sentences[:3])
    if style in {"selection", "domain", "pocket", "interface", "nature", "panels"}:
        return "\n".join(f"- {sentence}" for sentence in sentences[:5])
    return "\n".join(f"- {sentence}" for sentence in sentences[:6])


def format_legend_report(session, model_hint=None, style="text"):
    domains = [entry for entry in get_domain_selections(session, model_hint=model_hint) if entry["kind"] == "domain"]
    roles = get_role_selections(session, model_hint=model_hint)
    rows = []

    if domains:
        seen = set()
        for entry in domains[:8]:
            label = entry.get("display_label") or f"domain {entry['display_index']}"
            key = (entry["selection_name"], label)
            if key in seen:
                continue
            seen.add(key)
            rows.append(("domain", entry["selection_name"], label))

    if roles:
        for entry in roles:
            rows.append(("role", entry["selection_name"], entry["role"]))

    rows.extend(
        [
            ("site", "site_catalytic", "gold"),
            ("site", "site_ligand", "cornflowerblue"),
            ("site", "site_metal", "orange"),
            ("site", "site_interface", "hotpink"),
            ("site", "site_motif", "#ffb347"),
        ]
    )

    if not rows:
        return "- No current domain/role/site legend available."
    if style == "table":
        lines = ["kind\tname\tmeaning"]
        lines.extend(f"{kind}\t{name}\t{meaning}" for kind, name, meaning in rows)
        return "\n".join(lines)

    lines = []
    current_kind = None
    for kind, name, meaning in rows:
        if kind != current_kind:
            current_kind = kind
            title = {"domain": "- Domain colors", "role": "- Role colors", "site": "- Site highlight colors"}[kind]
            lines.append(title)
        lines.append(f"  - {name}: {meaning}")
    return "\n".join(lines)


def format_panel_plan(session, model_hint=None):
    semantics = get_session_semantics(session)
    selected_spec = resolve_model_spec(session, model_hint) if model_hint else None
    models = [model for model in semantics["models"] if model.get("atomic") and (not selected_spec or model["spec"] == selected_spec)]
    if not models:
        return "- No atomic models open."

    model = models[0]
    lines = [f"- Panel A: figure composite  ({model['name']} overall architecture)"]

    if get_ligand_sites(session, model_hint=model["spec"]) or get_metal_sites(session, model_hint=model["spec"]):
        lines.append("- Panel B: figure pocket  (active-site / cofactor context)")
    if best_interface_pair(session, model_hint=model["spec"]) is not None:
        lines.append("- Panel C: figure interface  (inter-chain contact surface)")
    if get_selection_overlap_payload(session, model_hint=model["spec"]) is not None:
        lines.append("- Panel D: figure selection-composite  (current selection with local hypotheses)")
    if len([entry for entry in get_domain_selections(session, model_hint=model["spec"]) if entry["kind"] == "chain"]) > 1:
        lines.append("- Panel E: figure explode-composite  (assembly arrangement)")
    if get_motif_hits(session, model_hint=model["spec"]):
        lines.append("- Panel F: figure selection-motif  (motif-centered close-up, if selection overlaps motif)")
    if "No DALI history yet." not in format_dali_report(session):
        lines.append("- Panel G: annotate / caption nature  (integrate parsed DALI structural-homology context)")
    return "\n".join(lines)


def format_dali_report(session):
    history = getattr(session, "_codex_bridge_dali_history", [])
    if not history:
        return "- No DALI history yet."
    item = history[-1]
    lines = ["- Latest DALI context"]
    for key in ("target", "file", "submit_url", "result_url", "summary"):
        value = item.get(key, "")
        if value:
            lines.append(f"  - {key}: {value}")
    parsed = item.get("parsed", {}) or {}
    for key in ("top_hit", "fold", "zscore", "rmsd", "aligned_length"):
        value = parsed.get(key, "")
        if value:
            lines.append(f"  - {key}: {value}")
    return "\n".join(lines)


def format_external_reference_report(session, model_hint=None):
    references = get_external_reference_payload(session, model_hint=model_hint)
    lines = ["- External reference context"]

    accessions = references.get("uniprot_accessions", [])
    if accessions:
        lines.append("  - UniProt accessions: " + ", ".join(accessions[:8]))

    model_entries = references.get("model_entries", [])
    if model_entries:
        lines.append("  - Model-linked RCSB entries:")
        for entry in model_entries[:4]:
            lines.append("    - " + _format_rcsb_reference_line(entry))

    dali_entries = references.get("dali_entries", [])
    if dali_entries:
        lines.append("  - DALI-linked RCSB hits:")
        for entry in dali_entries[:4]:
            lines.append("    - " + _format_rcsb_reference_line(entry))

    dali_summary = references.get("dali_summary")
    if dali_summary:
        lines.append("  - DALI parsed summary:")
        lines.extend(f"    - {line}" for line in dali_summary[:4])

    if len(lines) == 1:
        return "- No external retrieval context available."
    return "\n".join(lines)


def get_external_reference_payload(session, model_hint=None):
    from chimerax.atomic import AtomicStructure
    from chimerax.atomic.structure import uniprot_ids

    selected_spec = resolve_model_spec(session, model_hint) if model_hint else None
    model_entries = []
    seen_model_entries = set()
    accessions = set()

    for model in session.models.list(type=AtomicStructure):
        model_spec = f"#{getattr(model, 'id_string', '?')}"
        if selected_spec and model_spec != selected_spec:
            continue

        for useq in uniprot_ids(model):
            accession = getattr(useq, "uniprot_id", None)
            if accession:
                accessions.add(str(accession))

        for entry_id in _guess_model_pdb_ids(model):
            if entry_id in seen_model_entries:
                continue
            payload = _fetch_rcsb_entry_payload(session, entry_id)
            if not payload:
                continue
            model_entries.append(
                _rcsb_entry_summary(
                    entry_id,
                    payload,
                    source=f"{model_spec} {getattr(model, 'name', 'structure')}",
                )
            )
            seen_model_entries.add(entry_id)

    dali_entries = []
    seen_dali_entries = set()
    dali_summary = []
    history = getattr(session, "_codex_bridge_dali_history", []) or []
    if history:
        latest = history[-1]
        parsed = latest.get("parsed", {}) or {}
        for key in ("top_hit", "fold", "zscore", "rmsd", "aligned_length"):
            value = parsed.get(key)
            if value:
                dali_summary.append(f"{key}: {value}")
        candidate_texts = []
        for key in ("top_hit", "summary"):
            value = parsed.get(key) if key == "top_hit" else latest.get(key)
            if value:
                candidate_texts.append(str(value))
        for hit in parsed.get("hits", [])[:5]:
            title = hit.get("title")
            if title:
                candidate_texts.append(str(title))
        for text in candidate_texts:
            for entry_id in _extract_pdb_ids(text):
                if entry_id in seen_dali_entries:
                    continue
                payload = _fetch_rcsb_entry_payload(session, entry_id)
                if not payload:
                    continue
                dali_entries.append(
                    _rcsb_entry_summary(
                        entry_id,
                        payload,
                        source="DALI hit",
                    )
                )
                seen_dali_entries.add(entry_id)

    model_entries.sort(key=lambda item: item["entry_id"])
    dali_entries.sort(key=lambda item: item["entry_id"])
    return {
        "uniprot_accessions": sorted(accessions),
        "model_entries": model_entries,
        "dali_entries": dali_entries,
        "dali_summary": dali_summary,
    }


def format_figure_lab_report(session, model_hint=None):
    payload = get_selection_overlap_payload(session, model_hint=model_hint)
    lines = ["- In-app figure development"]

    if payload is not None:
        lines.append("- Current selection detected.")
        recommendations = []
        if payload["interface_matches"]:
            recommendations.append("figure selection-interface")
        if payload["motif_matches"]:
            recommendations.append("figure selection-motif")
        if payload["ligand_matches"] or payload["metal_matches"] or payload["catalytic_hits"]:
            recommendations.append("figure selection-pocket")
        if not recommendations:
            recommendations.append("figure selection")
        lines.append("- Recommended next figure commands:")
        lines.extend(f"  - {command}" for command in recommendations[:3])
        lines.append("- Recommended review flow:")
        lines.append("  - selected analyze")
        lines.append("  - legend")
        lines.append("  - caption selection")
        return "\n".join(lines)

    interfaces = best_interface_pair(session, model_hint=model_hint)
    ligand = best_ligand_site(session, model_hint=model_hint)
    metal = best_metal_site(session, model_hint=model_hint)
    domains = [entry for entry in get_domain_selections(session, model_hint=model_hint) if entry["kind"] == "domain"]

    lines.append("- No current selection; recommend global figure development.")
    recs = []
    if ligand or metal:
        recs.append("figure pocket")
        recs.append("figure composite")
    if interfaces is not None:
        recs.append("figure interface")
        recs.append("figure explode-composite")
    if domains:
        recs.append("figure domains")
    if not recs:
        recs.append("figure publication")
    deduped = []
    for cmd in recs:
        if cmd not in deduped:
            deduped.append(cmd)
    lines.append("- Recommended next figure commands:")
    lines.extend(f"  - {command}" for command in deduped[:4])
    lines.append("- Recommended review flow:")
    lines.append("  - panels")
    lines.append("  - legend")
    lines.append("  - caption nature")
    return "\n".join(lines)


def get_motif_hits(session, model_hint=None, motif_text=None):
    from chimerax.atomic import AtomicStructure, Residue

    selected_spec = resolve_model_spec(session, model_hint) if model_hint else None
    patterns = _resolved_motif_patterns(motif_text)
    hits = []

    for model in session.models.list(type=AtomicStructure):
        model_spec = f"#{getattr(model, 'id_string', '?')}"
        if selected_spec and model_spec != selected_spec:
            continue
        model_token = _safe_selection_token(model_spec.lstrip("#"))
        for chain in model.chains:
            polymer_type = getattr(chain, "polymer_type", None)
            if polymer_type not in (Residue.PT_AMINO, Residue.PT_PROTEIN):
                continue
            sequence_text = (getattr(chain, "characters", "") or "").upper()
            residues = chain.existing_residues
            numbers = [int(number) for number in residues.numbers]
            if not sequence_text or not numbers:
                continue
            usable_len = min(len(sequence_text), len(numbers))
            sequence_text = sequence_text[:usable_len]
            numbers = numbers[:usable_len]
            chain_id = str(chain.chain_id).strip() or "?"
            chain_token = _safe_selection_token(chain_id)

            for pattern in patterns:
                pattern_name, pattern_regex, category, description, priority = _motif_pattern_entry(pattern)
                for match_index, match in enumerate(_iter_motif_matches(pattern_regex, sequence_text), start=1):
                    start_idx, end_idx, matched_sequence = match
                    residue_numbers = numbers[start_idx:end_idx + 1]
                    if not residue_numbers:
                        continue
                    residue_specs = [
                        f"{model_spec}/{chain_id}:{number}"
                        for number in residue_numbers
                    ]
                    hits.append(
                        {
                            "model_spec": model_spec,
                            "model_name": getattr(model, "name", "structure"),
                            "chain_id": chain_id,
                            "pattern_name": pattern_name,
                            "pattern_regex": pattern_regex,
                            "category": category,
                            "description": description,
                            "priority": priority,
                            "matched_sequence": matched_sequence,
                            "start_number": residue_numbers[0],
                            "end_number": residue_numbers[-1],
                            "residue_specs": residue_specs,
                            "selection_name": (
                                f"motif_{model_token}_{chain_token}_{_safe_selection_token(pattern_name)}_{match_index}"
                            ),
                        }
                    )

    hits.sort(key=lambda item: (item["model_spec"], item["chain_id"], item["start_number"], item.get("priority", 99), item["pattern_name"]))
    return hits


def get_ligand_sites(session, model_hint=None, shell_cutoff=4.5):
    from chimerax.atomic import AtomicStructure

    selected_spec = resolve_model_spec(session, model_hint) if model_hint else None
    sites = []

    for model in session.models.list(type=AtomicStructure):
        spec = f"#{getattr(model, 'id_string', '?')}"
        if selected_spec and spec != selected_spec:
            continue

        atoms = model.atoms
        ligand_mask = atoms.structure_categories == "ligand"
        ligand_atoms = atoms.filter(ligand_mask)
        if len(ligand_atoms) == 0:
            continue

        ligand_residues = ligand_atoms.residues.unique()
        protein_mask = (atoms.structure_categories == "main") & (atoms.element_names != "H")
        protein_atoms = atoms.filter(protein_mask)
        if len(protein_atoms) == 0:
            continue

        for ligand_residue in ligand_residues[:8]:
            l_atoms = ligand_residue.atoms.filter(ligand_residue.atoms.element_names != "H")
            if len(l_atoms) == 0:
                continue
            l_center = l_atoms.scene_coords.mean(axis=0)
            distances = np.linalg.norm(protein_atoms.scene_coords - l_center, axis=1)
            nearby_atoms = protein_atoms.filter(distances <= shell_cutoff)
            nearby_distances = distances[distances <= shell_cutoff]
            residue_hits = _residue_distance_hits_with_distances(nearby_atoms, nearby_distances)
            catalytic = [hit for hit in residue_hits if hit["name"] in CATALYTIC_LIKE_RESNAMES]
            score = _site_score(len(catalytic), len(residue_hits), 0 if not residue_hits else residue_hits[0]["min_distance"])
            sites.append(
                {
                    "model_spec": spec,
                    "model_name": getattr(model, "name", "structure"),
                    "ligand_label": _residue_label(ligand_residue),
                    "ligand_spec": _residue_spec(spec, ligand_residue),
                    "ligand_chain": str(ligand_residue.chain_id).strip() or "?",
                    "nearby": residue_hits,
                    "catalytic_like": catalytic,
                    "score": score,
                }
            )
    sites.sort(key=lambda s: s["score"], reverse=True)
    return sites


def format_catalytic_report(session, model_hint=None):
    candidates = score_catalytic_residues(session, model_hint=model_hint)
    session._codex_bridge_last_catalytic_candidates = candidates[:12]
    if not candidates:
        if model_hint:
            return f"- No catalytic-like candidates found for: {model_hint}"
        return "- No catalytic-like residue candidates detected."
    lines = ["- Catalytic residue triage"]
    for candidate in candidates[:15]:
        reason_text = ", ".join(candidate["reasons"][:4])
        lines.append(
            f"  - {candidate['residue_spec']} {candidate['name']} "
            f"score {candidate['score']} [{candidate.get('consensus', 'candidate')}] ({reason_text})"
        )
    lines.append("  - inspect: /catalytic view | /motif view | /conservation view | /site catalytic")
    return "\n".join(lines)


def format_catalytic_workflow_report(session, model_hint=None):
    candidates = score_catalytic_residues(session, model_hint=model_hint)
    session._codex_bridge_last_catalytic_candidates = candidates[:12]
    lines = ["Catalytic residue workflow"]
    if candidates:
        lines.append("- Ranked candidates")
        for candidate in candidates[:10]:
            reasons = ", ".join(candidate["reasons"][:5])
            lines.append(
                f"  - {candidate['residue_spec']} {candidate['name']} "
                f"score {candidate['score']} [{candidate.get('consensus', 'candidate')}] ({reasons})"
            )
    else:
        lines.append("- Ranked candidates: none detected from local structure/motif evidence")

    selection = format_selection_overlap_report(session, model_hint=model_hint)
    if "No current ChimeraX selection." not in selection:
        lines.append("- Selection check")
        lines.extend(f"  {line}" for line in selection.splitlines()[:8])

    motif = format_motif_report(session, model_hint=model_hint)
    if "No motif-like sequence patterns detected" not in motif:
        lines.append("- Motif evidence")
        lines.extend(f"  {line}" for line in motif.splitlines()[:8])

    ligand = format_ligand_report(session, model_hint=model_hint)
    if "No ligand residues detected" not in ligand and "No ligand-bearing atomic model matched" not in ligand:
        lines.append("- Ligand-pocket evidence")
        lines.extend(f"  {line}" for line in ligand.splitlines()[:8])

    metal = format_metal_report(session, model_hint=model_hint)
    if "No metal ions detected" not in metal and "No metal-containing atomic model matched" not in metal:
        lines.append("- Metal-site evidence")
        lines.extend(f"  {line}" for line in metal.splitlines()[:8])

    conservation = _last_conservation_lines(session, model_hint=model_hint, limit=6)
    if conservation:
        lines.append("- Recent conservation evidence")
        lines.extend(f"  {line}" for line in conservation)

    lines.append("- Recommended verification loop")
    lines.append("  - /catalytic view")
    lines.append("  - /motif view")
    lines.append("  - /conservation view")
    lines.append("  - /hbonds site_catalytic")
    lines.append("  - /contacts site_catalytic")
    return "\n".join(lines)


def score_catalytic_residues(session, model_hint=None):
    scored = {}
    ligand_chains = set()
    metal_chains = set()
    motif_specs = set()
    motif_neighbor_specs = set()
    motif_chains = set()

    for site in get_metal_sites(session, model_hint=model_hint):
        metal_chains.add(site["metal_chain"])
        for hit in site["direct"]:
            _accumulate_residue_score(scored, site["model_spec"], hit, 4, "metal direct coordination")
        for hit in site["catalytic_like"]:
            _accumulate_residue_score(scored, site["model_spec"], hit, 2, "metal-shell catalytic-like")
        if site["direct"]:
            _accumulate_residue_score(scored, site["model_spec"], site["direct"][0], 2, "closest metal coordinator")

    for site in get_ligand_sites(session, model_hint=model_hint):
        ligand_chains.add(site["ligand_chain"])
        for hit in site["catalytic_like"]:
            _accumulate_residue_score(scored, site["model_spec"], hit, 3, "ligand-pocket catalytic-like")
        for hit in site["nearby"][:8]:
            _accumulate_residue_score(scored, site["model_spec"], hit, 1, "ligand-pocket nearby")
        if site["nearby"]:
            _accumulate_residue_score(scored, site["model_spec"], site["nearby"][0], 1, "closest ligand-shell residue")

    for motif in get_motif_hits(session, model_hint=model_hint):
        motif_chains.add(motif["chain_id"])
        for residue_spec in motif["residue_specs"]:
            motif_specs.add(residue_spec)
        motif_neighbor_specs.update(_motif_neighbor_specs(motif))
        if _motif_supports_catalytic_assignment(motif):
            motif_weight = 3 if _safe_int(motif.get("priority", 99), 99) <= 2 else 2
            for hit in _motif_catalytic_candidate_hits(motif):
                _accumulate_residue_score(scored, hit["model_spec"], hit, motif_weight, "catalytic motif residue")

    for hit in _catalytic_cluster_candidate_hits(session, model_hint=model_hint):
        _accumulate_residue_score(scored, hit["model_spec"], hit, 1, "3D catalytic-like cluster")

    candidates = list(scored.values())
    conserved_map = _last_conservation_score_map(session)
    for candidate in candidates:
        if candidate["residue_spec"] in motif_specs:
            candidate["score"] += 2
            candidate["reasons"].add("motif overlap")
        elif candidate["residue_spec"] in motif_neighbor_specs:
            candidate["score"] += 1
            candidate["reasons"].add("motif vicinity")
        if candidate["chain_id"] in motif_chains:
            candidate["score"] += 1
            candidate["reasons"].add("motif-bearing chain")
        if candidate["chain_id"] in ligand_chains:
            candidate["score"] += 1
            candidate["reasons"].add("ligand-bearing chain")
        if candidate["chain_id"] in metal_chains:
            candidate["score"] += 1
            candidate["reasons"].add("metal-bearing chain")
        if any(reason.startswith("motif") for reason in candidate["reasons"]) and any(
            "ligand" in reason or "metal" in reason for reason in candidate["reasons"]
        ):
            candidate["score"] += 1
            candidate["reasons"].add("motif-pocket consensus")
        conservation = conserved_map.get(candidate["residue_spec"])
        if conservation is not None:
            candidate["conservation_grade"] = conservation
            if conservation >= 8:
                candidate["score"] += 2
                candidate["reasons"].add("high conservation")
            elif conservation >= 7:
                candidate["score"] += 1
                candidate["reasons"].add("moderate conservation")
        candidate["reasons"] = sorted(candidate["reasons"])
        candidate["consensus"] = _consensus_label(candidate["reasons"])
    candidates.sort(key=lambda item: (-item["score"], item["residue_spec"]))
    return candidates


def best_catalytic_candidates(session, model_hint=None, limit=12):
    return score_catalytic_residues(session, model_hint=model_hint)[:limit]


def format_sequence_report(session, model_hint=None):
    semantics = get_session_semantics(session)
    selected_spec = resolve_model_spec(session, model_hint) if model_hint else None

    lines = []
    for model in semantics["models"]:
        if selected_spec and model["spec"] != selected_spec:
            continue
        if not model.get("atomic"):
            continue
        lines.append(f"- {model['spec']} {model['name']}")
        for chain in model["chains"]:
            lines.append(
                f"  - chain {chain['id']} [{chain['polymer_type']}] observed {chain['count']}, "
                f"full {chain['full_count']}, coverage {chain['coverage_percent']:.1f}%"
            )
            if chain["sequence_preview"]:
                lines.append(f"    - sequence: {chain['sequence_preview']}")
            if chain["missing_gaps"]:
                gap_text = ", ".join(f"{g[0]}-{g[1]} ({g[2]})" for g in chain["missing_gaps"][:6])
                lines.append(f"    - missing gaps: {gap_text}")
    if not lines:
        if model_hint:
            return f"- No atomic model matched: {model_hint}"
        return "- No atomic models open."
    return "\n".join(lines)


def format_domains_report(session, model_hint=None):
    semantics = get_session_semantics(session)
    selected_spec, selected_chains = _resolve_domain_target(session, model_hint)
    domain_entries = get_domain_selections(session, model_hint=model_hint)
    settings = get_domain_split_settings(session)
    domains_by_chain = {}
    for entry in domain_entries:
        if entry["kind"] != "domain":
            continue
        domains_by_chain.setdefault((entry["model_spec"], entry["chain_id"]), []).append(entry)

    lines = []
    for model in semantics["models"]:
        if selected_spec and model["spec"] != selected_spec:
            continue
        if not model.get("atomic"):
            continue
        lines.append(f"- {model['spec']} {model['name']}")
        lines.append(
            f"  - domain split settings: min_length {settings['min_length']}, "
            f"max_chunks {settings['max_chunks_per_chain']}"
        )
        for chain in model["chains"]:
            if selected_chains and chain["id"] not in selected_chains:
                continue
            lines.append(f"  - chain {chain['id']} [{chain['polymer_type']}]")
            filtered_chunks = domains_by_chain.get((model["spec"], chain["id"]), [])
            if filtered_chunks:
                for matching_entry in filtered_chunks[:10]:
                    lines.append(
                        f"    - chunk {matching_entry['display_index']}: {matching_entry['start']}-{matching_entry['end']} "
                        f"({matching_entry['length']} residues; {matching_entry['reason']})"
                        + (f" [{matching_entry['display_label']}]" if matching_entry.get("display_label") else "")
                        + (f" -> selection {matching_entry['selection_name']}" if matching_entry.get("selection_name") else "")
                    )
            else:
                lines.append("    - no chunk boundaries detected at current split settings")
        if any(entry["model_spec"] == model["spec"] for entry in domain_entries):
            lines.append("  - visualize: use `/domains view` or `figure domains` to create named selections and domain colors")
    if not lines:
        if model_hint:
            return f"- No atomic model matched: {model_hint}"
        return "- No atomic models open."
    return "\n".join(lines)


def format_complex_report(session, model_hint=None, contact_cutoff=8.0):
    from chimerax.atomic import AtomicStructure

    selected_spec = resolve_model_spec(session, model_hint) if model_hint else None
    lines = []

    for model in session.models.list(type=AtomicStructure):
        spec = f"#{getattr(model, 'id_string', '?')}"
        if selected_spec and spec != selected_spec:
            continue
        interfaces = get_complex_interfaces(model, contact_cutoff=contact_cutoff)
        if not interfaces:
            continue
        lines.append(f"- {spec} {getattr(model, 'name', 'structure')}")
        for line in _complex_sequence_group_lines_for_structure(model):
            lines.append(f"  - {line}")
        for report in interfaces:
            lines.append(
                f"  - interface {report['chain_a']}-{report['chain_b']}: min {report['min_distance']:.2f} A, "
                f"{report['contacts_a']}+{report['contacts_b']} contact-like residues"
            )
        for line in _complex_role_lines(interfaces):
            lines.append(f"  - {line}")
        for line in _complex_site_role_lines(session, spec, interfaces):
            lines.append(f"  - {line}")

    if not lines:
        if model_hint:
            return f"- No multi-chain complex matched: {model_hint}"
        return "- No chain-chain interfaces detected."
    return "\n".join(lines)


def format_roles_report(session, model_hint=None):
    from chimerax.atomic import AtomicStructure

    selected_spec = resolve_model_spec(session, model_hint) if model_hint else None
    lines = []
    for model in session.models.list(type=AtomicStructure):
        spec = f"#{getattr(model, 'id_string', '?')}"
        if selected_spec and spec != selected_spec:
            continue
        role_lines = _role_lines_for_model(session, model)
        if not role_lines:
            continue
        lines.append(f"- {spec} {getattr(model, 'name', 'structure')}")
        lines.extend(f"  - {line}" for line in role_lines)
    if not lines:
        if model_hint:
            return f"- No role-like chain assignments detected for: {model_hint}"
        return "- No role-like chain assignments detected."
    return "\n".join(lines)


def format_analyze_report(session, model_hint=None):
    semantics = get_session_semantics(session)
    selected_spec = resolve_model_spec(session, model_hint) if model_hint else None
    selection_report = format_selection_overlap_report(session, model_hint)

    lines = []
    for model in semantics["models"]:
        if selected_spec and model["spec"] != selected_spec:
            continue
        if not model.get("atomic"):
            continue
        lines.extend(_analysis_lines_for_model(model))
        lines.extend(_domain_summary_lines(model))

    if not lines:
        if model_hint:
            return f"- No atomic model matched: {model_hint}"
        return "- No atomic models open."
    if "No current ChimeraX selection." not in selection_report:
        lines.extend(["- selection context", *selection_report.splitlines()])
    return "\n".join(lines)


def format_metal_report(session, model_hint=None, direct_cutoff=3.0, shell_cutoff=5.0):
    sites = get_metal_sites(session, model_hint=model_hint, direct_cutoff=direct_cutoff, shell_cutoff=shell_cutoff)
    if not sites:
        if model_hint:
            return f"- No metal-containing atomic model matched: {model_hint}"
        return "- No metal ions detected in open atomic models."

    lines = []
    current_model = None
    for site in sites:
        if current_model != site["model_spec"]:
            current_model = site["model_spec"]
            lines.append(f"- {site['model_spec']} {site['model_name']}")
            lines.extend(_uniprot_lines(site["uniprot"]))
        lines.append(f"  - metal site: {site['metal_label']} (score {site['score']})")
        if site["direct"]:
            lines.append("    - direct coordinators: " + ", ".join(_format_residue_hit(hit) for hit in site["direct"][:8]))
        else:
            lines.append("    - direct coordinators: none within %.1f A" % direct_cutoff)
        if site["catalytic_like"]:
            lines.append("    - catalytic-like nearby: " + ", ".join(_format_residue_hit(hit) for hit in site["catalytic_like"][:10]))
        else:
            lines.append("    - catalytic-like nearby: none within %.1f A" % shell_cutoff)
    return "\n".join(lines)


def get_metal_sites(session, model_hint=None, direct_cutoff=3.0, shell_cutoff=5.0):
    from chimerax.atomic import AtomicStructure
    from chimerax.atomic.structure import uniprot_ids

    selected_spec = resolve_model_spec(session, model_hint) if model_hint else None
    sites = []
    for model in session.models.list(type=AtomicStructure):
        spec = f"#{getattr(model, 'id_string', '?')}"
        if selected_spec and spec != selected_spec:
            continue
        atoms = model.atoms
        if len(atoms) == 0:
            continue
        metal_mask = (atoms.structure_categories == "ions") & np.isin(atoms.element_names, list(METAL_ELEMENTS))
        metal_atoms = atoms.filter(metal_mask)
        if len(metal_atoms) == 0:
            continue
        heavy = atoms.filter(atoms.element_names != "H")
        if len(heavy) == 0:
            continue
        heavy_coords = heavy.scene_coords
        uniprot = uniprot_ids(model)
        for idx, metal_atom in enumerate(metal_atoms):
            mcoord = metal_atoms.scene_coords[idx]
            distances = np.linalg.norm(heavy_coords - mcoord, axis=1)
            nearby = heavy.filter((distances <= shell_cutoff))
            residue_hits = _residue_distance_hits(nearby, mcoord, metal_atom)
            direct = [hit for hit in residue_hits if hit["min_distance"] <= direct_cutoff]
            catalytic = [hit for hit in residue_hits if hit["name"] in CATALYTIC_LIKE_RESNAMES]
            sites.append(
                {
                    "model_spec": spec,
                    "model_name": getattr(model, "name", "structure"),
                    "uniprot": uniprot,
                    "metal_label": _atom_label(metal_atom),
                    "metal_spec": getattr(metal_atom, "atomspec", _residue_spec(spec, metal_atom.residue)),
                    "metal_chain": str(metal_atom.residue.chain_id).strip() or "?",
                    "direct": direct,
                    "catalytic_like": catalytic,
                    "site_residue_specs": _site_residue_specs(spec, direct or catalytic),
                    "score": _site_score(len(direct), len(catalytic), 0 if not direct else direct[0]["min_distance"]),
                }
            )
    sites.sort(key=lambda s: s["score"], reverse=True)
    return sites


def format_chains_report(session, model_hint=None):
    semantics = get_session_semantics(session)
    selected_spec = resolve_model_spec(session, model_hint) if model_hint else None

    lines = []
    for model in semantics["models"]:
        if selected_spec and model["spec"] != selected_spec:
            continue
        if not model.get("atomic"):
            continue
        lines.append(f"- {model['spec']} {model['name']}")
        for chain in model["chains"]:
            if chain["start"] is None:
                lines.append(f"  - chain {chain['id']} ({chain['count']} residues)")
            else:
                lines.append(
                    f"  - chain {chain['id']} {chain['start']}-{chain['end']} ({chain['count']} residues)"
                )
    if not lines:
        if model_hint:
            return f"- No atomic model matched: {model_hint}"
        return "- No atomic models open."
    return "\n".join(lines)


def _collect_session_semantics(session):
    from chimerax.atomic import AtomicStructure, Residue, selected_atoms, selected_residues

    models = []
    aliases = {}

    for model in session.models.list():
        spec = f"#{getattr(model, 'id_string', '?')}"
        name = getattr(model, "name", model.__class__.__name__)
        normalized_name = _normalize(name)
        aliases.setdefault(_normalize(spec), spec)
        if normalized_name:
            aliases.setdefault(normalized_name, spec)

        entry = {
            "spec": spec,
            "name": name,
            "type": model.__class__.__name__,
            "visible": bool(getattr(model, "visible", False)),
            "id_alias": _normalize(spec),
            "name_aliases": tuple(filter(None, {normalized_name, _normalize(getattr(model, "id_string", ""))})),
        }

        if isinstance(model, AtomicStructure):
            entry["atomic"] = True
            entry["atoms"] = len(model.atoms)
            entry["residues"] = len(model.residues)
            entry["chains"] = _collect_chains(model, Residue)
            entry["categories"] = _collect_structure_categories(model)
            entry["ligands"] = _collect_residue_names(model, "ligand")
            entry["ions"] = _collect_residue_names(model, "ions")
            entry["solvent"] = _collect_residue_names(model, "solvent")
        else:
            entry["atomic"] = False
            entry["chains"] = []
            entry["categories"] = {}
            entry["ligands"] = []
            entry["ions"] = []
            entry["solvent"] = []
        models.append(entry)

    sel_models = [f"#{getattr(m, 'id_string', '?')}" for m in session.selection.models()]
    sel_atoms = selected_atoms(session)
    sel_residues = selected_residues(session)

    selection = {
        "models": sel_models,
        "atoms": len(sel_atoms),
        "residues": len(sel_residues),
        "ranges": _selected_ranges(sel_residues),
    }

    return {
        "models": models,
        "aliases": aliases,
        "selection": selection,
    }


def _collect_chains(model, Residue):
    chains = []
    for chain in model.chains:
        residues = chain.existing_residues
        numbers = residues.numbers
        sequence_text = getattr(chain, "characters", "") or ""
        chains.append(
            {
                "id": "?" if not str(chain.chain_id).strip() else str(chain.chain_id),
                "count": int(chain.num_existing_residues),
                "full_count": int(chain.num_residues),
                "start": int(numbers.min()) if len(numbers) else None,
                "end": int(numbers.max()) if len(numbers) else None,
                "description": chain.description or "",
                "polymer_type": _polymer_type_name(chain.polymer_type, Residue),
                "sequence_text": sequence_text,
                "sequence_preview": _sequence_preview(sequence_text),
                "coverage_percent": (100.0 * int(chain.num_existing_residues) / max(int(chain.num_residues), 1)),
                "missing_gaps": _missing_gaps(numbers),
                "chunks": _chain_chunks(residues),
            }
        )
    return chains


def _collect_structure_categories(model):
    categories = Counter()
    atoms = model.atoms
    for category in atoms.structure_categories:
        if category:
            categories[str(category)] += 1
    return dict(categories)


def _collect_residue_names(model, category):
    atoms = model.atoms.filter(model.atoms.structure_categories == category)
    residues = atoms.residues.unique()
    counts = Counter(str(name) for name in residues.names)
    return counts.most_common(8)


def _selected_ranges(selected_residues):
    ranges = []
    for structure, chain_id, residues in selected_residues.by_chain:
        numbers = residues.numbers
        if len(numbers) == 0:
            continue
        chain_label = "?" if not str(chain_id).strip() else str(chain_id)
        if len(numbers) == 1:
            ranges.append(f"#{structure.id_string}/{chain_label}:{int(numbers[0])}")
        else:
            ranges.append(f"#{structure.id_string}/{chain_label}:{int(numbers.min())}-{int(numbers.max())}")
    return ranges


def _model_lines(semantics):
    lines = [f"- Open models: {len(semantics['models'])}"]
    for model in semantics["models"][:12]:
        line = f"  - {model['spec']} {model['name']} [{model['type']}; {'visible' if model['visible'] else 'hidden'}]"
        if model["atomic"]:
            line += f" {model['atoms']} atoms, {model['residues']} residues"
        lines.append(line)
        for chain in model["chains"][:6]:
            chain_line = f"    - chain {chain['id']} [{chain['polymer_type']}]"
            if chain["start"] is not None:
                chain_line += f" {chain['start']}-{chain['end']}"
            chain_line += f" ({chain['count']} residues"
            if chain["full_count"] != chain["count"]:
                chain_line += f", full {chain['full_count']}"
            chain_line += ")"
            if chain["description"]:
                chain_line += f" {chain['description']}"
            lines.append(chain_line)
        if len(model["chains"]) > 6:
            lines.append(f"    - ... {len(model['chains']) - 6} more chains omitted")
        category_bits = _category_bits(model["categories"])
        if category_bits:
            lines.append("    - categories: " + ", ".join(category_bits))
        if model["ligands"]:
            lines.append("    - ligands: " + ", ".join(f"{name} x{count}" for name, count in model["ligands"]))
        if model["ions"]:
            lines.append("    - ions: " + ", ".join(f"{name} x{count}" for name, count in model["ions"]))
    if len(semantics["models"]) > 12:
        lines.append(f"  - ... {len(semantics['models']) - 12} more models omitted")
    return lines


def _selection_lines(semantics):
    selection = semantics["selection"]
    lines = [
        f"- Selected models: {', '.join(selection['models']) if selection['models'] else '(none)'}",
        f"- Selected atoms: {selection['atoms']}",
        f"- Selected residues: {selection['residues']}",
    ]
    if selection["ranges"]:
        lines.append("- Selected ranges: " + ", ".join(selection["ranges"][:10]))
        if len(selection["ranges"]) > 10:
            lines.append(f"- ... {len(selection['ranges']) - 10} more selected ranges omitted")
    return lines


def _normalize(text):
    return re.sub(r"[^a-z0-9#]+", "", str(text).lower())


def _polymer_type_name(polymer_type, Residue):
    if polymer_type == Residue.PT_AMINO or polymer_type == Residue.PT_PROTEIN:
        return "protein"
    if polymer_type == Residue.PT_NUCLEIC:
        return "nucleic"
    if polymer_type == Residue.PT_NONE:
        return "non-polymer"
    return f"type-{polymer_type}"


def _category_bits(categories):
    important = []
    for key in ("main", "ligand", "ions", "solvent"):
        count = categories.get(key)
        if count:
            important.append(f"{key} {count}")
    return important


def _analysis_lines_for_model(model):
    lines = [
        f"- {model['spec']} {model['name']}",
        f"  - atoms: {model['atoms']}",
        f"  - residues: {model['residues']}",
        f"  - visible: {'yes' if model['visible'] else 'no'}",
    ]
    protein_chains = [c for c in model["chains"] if c["polymer_type"] == "protein"]
    nucleic_chains = [c for c in model["chains"] if c["polymer_type"] == "nucleic"]
    lines.append(f"  - chains: {len(model['chains'])} total, {len(protein_chains)} protein, {len(nucleic_chains)} nucleic")
    for chain in model["chains"][:10]:
        desc = f" {chain['description']}" if chain["description"] else ""
        if chain["start"] is None:
            lines.append(f"    - chain {chain['id']} [{chain['polymer_type']}] ({chain['count']} residues){desc}")
        else:
            lines.append(
                f"    - chain {chain['id']} [{chain['polymer_type']}] {chain['start']}-{chain['end']} "
                f"({chain['count']} residues){desc}"
            )
    if len(model["chains"]) > 10:
        lines.append(f"    - ... {len(model['chains']) - 10} more chains omitted")
    category_bits = _category_bits(model["categories"])
    if category_bits:
        lines.append("  - categories: " + ", ".join(category_bits))
    if model["ligands"]:
        lines.append("  - ligand residues: " + ", ".join(f"{name} x{count}" for name, count in model["ligands"]))
    if model["ions"]:
        lines.append("  - ion residues: " + ", ".join(f"{name} x{count}" for name, count in model["ions"]))
    if model["solvent"]:
        lines.append("  - solvent residues: " + ", ".join(f"{name} x{count}" for name, count in model["solvent"][:6]))
    return lines


def _domain_summary_lines(model):
    lines = []
    for chain in model["chains"]:
        if chain["chunks"]:
            summary = ", ".join(
                f"{chunk['start']}-{chunk['end']} ({chunk['reason']})" for chunk in chain["chunks"][:4]
            )
            lines.append(f"  - chain {chain['id']} domain-like chunks: {summary}")
    return lines


def _uniprot_lines(useqs):
    if not useqs:
        return ["  - UniProt mapping: none detected"]
    lines = []
    seen = set()
    for useq in useqs:
        key = (useq.chain_id, useq.uniprot_id, useq.uniprot_name)
        if key in seen:
            continue
        seen.add(key)
        lines.append(
            f"  - UniProt: chain {useq.chain_id} -> {useq.uniprot_id} ({useq.uniprot_name})"
        )
    return lines


def _residue_distance_hits(nearby_atoms, metal_coord, metal_atom):
    residue_map = {}
    for atom in nearby_atoms:
        residue = atom.residue
        if residue == metal_atom.residue:
            continue
        key = (residue.chain_id, residue.number, residue.name)
        dist = float(np.linalg.norm(atom.scene_coord - metal_coord))
        hit = residue_map.get(key)
        atom_name = atom.name
        if hit is None:
            residue_map[key] = {
                "chain_id": residue.chain_id,
                "number": residue.number,
                "name": residue.name,
                "min_distance": dist,
                "atom_names": {atom_name},
            }
        else:
            hit["min_distance"] = min(hit["min_distance"], dist)
            hit["atom_names"].add(atom_name)
    hits = list(residue_map.values())
    hits.sort(key=lambda h: h["min_distance"])
    return hits


def _residue_distance_hits_with_distances(nearby_atoms, distances):
    residue_map = {}
    for atom, dist in zip(nearby_atoms, distances):
        residue = atom.residue
        key = (str(residue.chain_id), int(residue.number), str(residue.name))
        if key not in residue_map:
            residue_map[key] = {
                "chain_id": str(residue.chain_id).strip() or "?",
                "number": int(residue.number),
                "name": str(residue.name),
                "min_distance": float(dist),
                "atom_names": {atom.name},
            }
        else:
            hit = residue_map[key]
            hit["min_distance"] = min(hit["min_distance"], float(dist))
            hit["atom_names"].add(atom.name)
    hits = list(residue_map.values())
    hits.sort(key=lambda h: h["min_distance"])
    return hits


def _format_residue_hit(hit):
    atom_names = ",".join(sorted(hit["atom_names"]))
    return f"{hit['name']} {hit['chain_id']}{hit['number']} ({hit['min_distance']:.2f} A via {atom_names})"


def _atom_label(atom):
    residue = atom.residue
    chain_id = residue.chain_id if str(residue.chain_id).strip() else "?"
    return f"{atom.element.name} {chain_id}{residue.number}:{atom.name}"


def _residue_label(residue):
    chain_id = str(residue.chain_id).strip() or "?"
    return f"{residue.name} {chain_id}{int(residue.number)}"


def _sequence_preview(sequence, width=18):
    if not sequence:
        return ""
    if len(sequence) <= width * 2 + 3:
        return sequence
    return f"{sequence[:width]}...{sequence[-width:]}"


def _missing_gaps(numbers):
    if len(numbers) < 2:
        return []
    sorted_nums = sorted(int(n) for n in numbers)
    gaps = []
    for prev_n, next_n in zip(sorted_nums[:-1], sorted_nums[1:]):
        if next_n - prev_n > 1:
            gaps.append((prev_n + 1, next_n - 1, next_n - prev_n - 1))
    return gaps


def _chain_chunks(residues):
    if len(residues) == 0:
        return []
    numbers = [int(n) for n in residues.numbers]
    is_structured = [bool(h or s) for h, s in zip(residues.is_helix, residues.is_strand)]
    break_points = set()

    for start, end, size in _missing_gaps(numbers):
        if size >= 20:
            break_points.add(start - 1)

    window = 25
    for i in range(0, max(len(numbers) - window, 0)):
        chunk = is_structured[i:i + window]
        if sum(chunk) <= 2:
            break_points.add(numbers[i + window // 2])

    chunks = []
    chunk_start = numbers[0]
    for prev_n, curr_n in zip(numbers[:-1], numbers[1:]):
        if prev_n in break_points:
            chunks.append(
                {
                    "index": len(chunks) + 1,
                    "start": chunk_start,
                    "end": prev_n,
                    "length": prev_n - chunk_start + 1,
                    "reason": "gap/linker break",
                }
            )
            chunk_start = curr_n
    chunks.append(
        {
            "index": len(chunks) + 1,
            "start": chunk_start,
            "end": numbers[-1],
            "length": numbers[-1] - chunk_start + 1,
            "reason": "contiguous segment" if chunks else "single segment",
        }
    )
    return chunks


def get_domain_selections(session, model_hint=None, min_length=None, max_chunks_per_chain=None):
    if min_length is None or max_chunks_per_chain is None:
        settings = get_domain_split_settings(session)
        if min_length is None:
            min_length = settings["min_length"]
        if max_chunks_per_chain is None:
            max_chunks_per_chain = settings["max_chunks_per_chain"]
    semantics = get_session_semantics(session)
    selected_spec, selected_chains = _resolve_domain_target(session, model_hint)

    entries = []
    for model in semantics["models"]:
        if selected_spec and model["spec"] != selected_spec:
            continue
        if not model.get("atomic"):
            continue

        model_token = _safe_selection_token(model["spec"].lstrip("#"))
        active_chain_ids = _active_chain_ids_for_model(session, model["spec"])
        for chain in model["chains"]:
            chain_id = chain["id"]
            if selected_chains and chain_id not in selected_chains:
                continue
            chain_token = _safe_selection_token(chain_id)
            chain_spec = f"{model['spec']}/{chain_id}"
            entries.append(
                {
                    "kind": "chain",
                    "model_spec": model["spec"],
                    "model_name": model["name"],
                    "chain_id": chain_id,
                    "spec": chain_spec,
                    "selection_name": f"chain_{model_token}_{chain_token}",
                    "group_name": f"group_{model_token}_chain_{chain_token}",
                    "count": chain["count"],
                    "label_spec": f"{chain_spec}:{chain['start']}" if chain["start"] is not None else None,
                }
            )

            meaningful_chunks = _meaningful_chunks(
                chain["chunks"],
                chain_length=chain["count"],
                min_length=min_length,
                max_chunks=max_chunks_per_chain,
            )
            total_chunks = len(meaningful_chunks)
            for display_index, chunk in enumerate(meaningful_chunks, start=1):
                display_label = _guess_domain_label(
                    display_index,
                    total_chunks,
                    chain["count"],
                    chain_id in active_chain_ids,
                )
                residue_range = (
                    f"{chunk['start']}"
                    if chunk["start"] == chunk["end"]
                    else f"{chunk['start']}-{chunk['end']}"
                )
                entries.append(
                    {
                        "kind": "domain",
                        "model_spec": model["spec"],
                        "model_name": model["name"],
                        "chain_id": chain_id,
                        "spec": f"{chain_spec}:{residue_range}",
                        "selection_name": f"domain_{model_token}_{chain_token}_{display_index}",
                        "group_name": f"group_{model_token}_domain_{chain_token}_{display_index}",
                        "chunk_index": chunk["index"],
                        "display_index": display_index,
                        "start": chunk["start"],
                        "end": chunk["end"],
                        "length": chunk["length"],
                        "reason": chunk["reason"],
                        "label_spec": f"{chain_spec}:{chunk['start']}",
                        "display_label": display_label,
                        "group_label": _slug_group_label(display_label),
                    }
                )
    return entries


def _resolve_domain_target(session, model_hint=None):
    source = str(model_hint or "").strip()
    semantics = get_session_semantics(session)

    selected_spec = resolve_model_spec(session, source) if source else None
    chain_filter = []

    chain_specs = extract_chain_specs_from_text(session, source, model_hint=selected_spec) if source else []
    residue_specs = extract_residue_specs_from_text(session, source, model_hint=selected_spec) if source else []

    inferred_specs = [*chain_specs, *residue_specs]
    if inferred_specs and not selected_spec:
        selected_spec = inferred_specs[0].split("/", 1)[0]

    for spec in inferred_specs:
        chain_id = _chain_id_from_atom_spec(spec)
        if chain_id and chain_id not in chain_filter:
            chain_filter.append(chain_id)

    selection = semantics.get("selection", {})
    selection_ranges = list(selection.get("ranges", []))
    if selection_ranges:
        selection_model = selection_ranges[0].split("/", 1)[0]
        selection_chains = []
        for spec in selection_ranges:
            if spec.split("/", 1)[0] != selection_model:
                selection_chains = []
                break
            chain_id = _chain_id_from_atom_spec(spec)
            if chain_id and chain_id not in selection_chains:
                selection_chains.append(chain_id)
        if selection_model and not selected_spec:
            selected_spec = selection_model
        if selection_model and selection_chains and selection_model == selected_spec and not chain_filter:
            chain_filter = selection_chains

    return selected_spec, tuple(chain_filter)


def _chain_id_from_atom_spec(spec):
    text = str(spec or "")
    if "/" not in text:
        return None
    suffix = text.split("/", 1)[1]
    chain_id = suffix.split(":", 1)[0].strip()
    return chain_id or None


def get_domain_split_settings(session):
    min_length = int(getattr(session, "_codex_bridge_domain_min_length", DEFAULT_DOMAIN_MIN_LENGTH) or DEFAULT_DOMAIN_MIN_LENGTH)
    max_chunks = int(getattr(session, "_codex_bridge_domain_max_chunks", DEFAULT_DOMAIN_MAX_CHUNKS) or DEFAULT_DOMAIN_MAX_CHUNKS)
    return {
        "min_length": min_length,
        "max_chunks_per_chain": max_chunks,
    }


def get_role_selections(session, model_hint=None):
    from chimerax.atomic import AtomicStructure

    semantics = get_session_semantics(session)
    chain_ranges = {}
    for model in semantics["models"]:
        if not model.get("atomic"):
            continue
        for chain in model["chains"]:
            chain_ranges[(model["spec"], chain["id"])] = chain

    selected_spec = resolve_model_spec(session, model_hint) if model_hint else None
    entries = []

    for model in session.models.list(type=AtomicStructure):
        model_spec = f"#{getattr(model, 'id_string', '?')}"
        if selected_spec and model_spec != selected_spec:
            continue

        ligand_sites = get_ligand_sites(session, model_hint=model_spec)
        metal_sites = get_metal_sites(session, model_hint=model_spec)
        catalytic = score_catalytic_residues(session, model_hint=model_spec)
        interfaces = get_complex_interfaces(model)

        ligand_chains = sorted({site["ligand_chain"] for site in ligand_sites})
        metal_chains = sorted({site["metal_chain"] for site in metal_sites})
        catalytic_chains = sorted({candidate["chain_id"] for candidate in catalytic[:12]})
        active_chains = sorted(set(ligand_chains) | set(metal_chains) | set(catalytic_chains))

        degree = Counter()
        for item in interfaces:
            degree[item["chain_a"]] += 1
            degree[item["chain_b"]] += 1

        core_chains = []
        peripheral_chains = []
        if degree:
            max_degree = max(degree.values())
            core_chains = sorted([cid for cid, value in degree.items() if value == max_degree])
            peripheral_chains = sorted([cid for cid, value in degree.items() if value < max_degree])

        scaffold_chains = sorted([cid for cid in core_chains if cid not in active_chains])
        accessory_chains = sorted([cid for cid in peripheral_chains if cid not in active_chains])
        groups = [
            ("active", active_chains),
            ("scaffold", scaffold_chains),
            ("peripheral", accessory_chains),
        ]
        if not any(chain_ids for _role, chain_ids in groups):
            continue

        model_token = _safe_selection_token(model_spec.lstrip("#"))
        for role_name, chain_ids in groups:
            if not chain_ids:
                continue
            entries.append(
                {
                    "role": role_name,
                    "model_spec": model_spec,
                    "model_name": getattr(model, "name", "structure"),
                    "chain_ids": chain_ids,
                    "spec": " ".join(f"{model_spec}/{chain_id}" for chain_id in chain_ids),
                    "selection_name": f"role_{role_name}_{model_token}",
                    "group_name": f"group_{model_token}_role_{role_name}",
                    "label_specs": [
                        f"{model_spec}/{chain_id}:{chain_ranges[(model_spec, chain_id)]['start']}"
                        for chain_id in chain_ids
                        if chain_ranges.get((model_spec, chain_id), {}).get("start") is not None
                    ],
                }
            )

    return entries


def _meaningful_chunks(chunks, chain_length, min_length=35, max_chunks=6):
    if not chunks:
        return []

    meaningful = [dict(chunk) for chunk in chunks if chunk["length"] >= min_length]
    if not meaningful:
        meaningful = [dict(chunk) for chunk in sorted(chunks, key=lambda chunk: (-chunk["length"], chunk["start"]))[:max_chunks]]

    meaningful = sorted(meaningful, key=lambda chunk: chunk["start"])
    if len(meaningful) == 1 and chain_length < min_length * 2:
        return []
    meaningful = _subdivide_meaningful_chunks(meaningful, min_length=min_length, max_chunks=max_chunks)
    normalized = []
    for index, chunk in enumerate(meaningful[:max_chunks], start=1):
        normalized.append(
            {
                **chunk,
                "index": index,
                "length": int(chunk["end"]) - int(chunk["start"]) + 1,
            }
        )
    return normalized


def _subdivide_meaningful_chunks(chunks, min_length=35, max_chunks=6):
    refined = [dict(chunk) for chunk in chunks]
    while len(refined) < max_chunks:
        candidates = []
        for index, chunk in enumerate(refined):
            length = int(chunk["end"]) - int(chunk["start"]) + 1
            if length >= max(min_length * 2, min_length + 20):
                candidates.append((length, index, chunk))
        if not candidates:
            break
        _, index, chunk = max(candidates, key=lambda item: item[0])
        start = int(chunk["start"])
        end = int(chunk["end"])
        split_point = (start + end) // 2
        left = {
            **chunk,
            "start": start,
            "end": split_point,
            "reason": f"{chunk['reason']}; refined split",
        }
        right = {
            **chunk,
            "start": split_point + 1,
            "end": end,
            "reason": f"{chunk['reason']}; refined split",
        }
        if (left["end"] - left["start"] + 1) < min_length or (right["end"] - right["start"] + 1) < min_length:
            break
        refined[index:index + 1] = [left, right]
        refined.sort(key=lambda item: item["start"])
    return refined


def _active_chain_ids_for_model(session, model_spec):
    ligand_chains = {site["ligand_chain"] for site in get_ligand_sites(session, model_hint=model_spec)}
    metal_chains = {site["metal_chain"] for site in get_metal_sites(session, model_hint=model_spec)}
    catalytic_chains = {candidate["chain_id"] for candidate in score_catalytic_residues(session, model_hint=model_spec)[:12]}
    return set(ligand_chains) | set(metal_chains) | set(catalytic_chains)


def _guess_domain_label(display_index, total_chunks, chain_length, active_chain):
    if total_chunks <= 1:
        return "single-chain segment"
    if active_chain and total_chunks >= 3 and 1 < display_index < total_chunks:
        return "catalytic-core candidate"
    if display_index == 1:
        return "N-terminal accessory domain" if total_chunks > 2 else "N-terminal domain"
    if display_index == total_chunks:
        return "C-terminal accessory domain" if total_chunks > 2 else "C-terminal domain"
    if total_chunks >= 3:
        return "insert/linker-side domain"
    if active_chain and chain_length >= 250:
        return "catalytic-core candidate"
    return "central domain"


def _motif_neighbor_specs(motif):
    residue_specs = []
    model_spec = motif["model_spec"]
    chain_id = motif["chain_id"]
    for offset in (-2, -1, 1, 2):
        residue_specs.append(f"{model_spec}/{chain_id}:{motif['start_number'] + offset}")
        residue_specs.append(f"{model_spec}/{chain_id}:{motif['end_number'] + offset}")
    return residue_specs


def _motif_feature_overlaps(motif_hits, feature_entries):
    overlaps = []
    for hit in motif_hits:
        hit_start = int(hit["start_number"])
        hit_end = int(hit["end_number"])
        for entry in feature_entries:
            if entry["model_spec"] != hit["model_spec"] or entry["chain_id"] != hit["chain_id"]:
                continue
            start = int(entry["start"])
            end = int(entry["end"])
            overlap_start = max(hit_start, start)
            overlap_end = min(hit_end, end)
            if overlap_start > overlap_end:
                continue
            overlaps.append(
                {
                    "model_spec": hit["model_spec"],
                    "chain_id": hit["chain_id"],
                    "motif_label": f"{hit['pattern_name']} [{hit['matched_sequence']}]",
                    "feature_type": entry["feature_type"],
                    "feature_label": entry["label"],
                    "overlap_text": f"{overlap_start}-{overlap_end}",
                }
            )
    overlaps.sort(key=lambda item: (item["model_spec"], item["chain_id"], item["overlap_text"], item["motif_label"]))
    return overlaps


def _safe_selection_token(text):
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", str(text)).strip("_")
    return normalized.lower() or "x"


def _slug_group_label(text):
    return re.sub(r"[^A-Za-z0-9]+", "_", str(text or "")).strip("_").lower()


def _chain_token_from_spec(spec):
    text = str(spec or "")
    if "/" not in text:
        return text
    model_spec, chain_part = text.split("/", 1)
    chain_id = chain_part.split(":", 1)[0]
    return f"{model_spec}/{chain_id}"


def _spec_matches_selection(spec, selected_specs, selected_chain_tokens):
    token = str(spec or "").strip()
    if not token:
        return False
    if token in selected_specs:
        return True
    chain_token = _chain_token_from_spec(token)
    if chain_token in selected_chain_tokens:
        return True
    return any(token.startswith(selected + ":") for selected in selected_specs if ":" in selected)


def _resolved_motif_patterns(motif_text=None):
    if motif_text is None:
        return DEFAULT_MOTIF_PATTERNS

    token = str(motif_text).strip()
    if not token or token.lower() in {"auto", "default"}:
        return DEFAULT_MOTIF_PATTERNS

    return [(token.upper(), _motif_text_to_regex(token), "custom", "user-specified sequence motif", 0)]


def _motif_pattern_entry(pattern):
    if isinstance(pattern, dict):
        return (
            str(pattern.get("name", "motif")),
            str(pattern.get("regex", "")),
            str(pattern.get("category", "motif")),
            str(pattern.get("description", "")),
            int(pattern.get("priority", 5)),
        )
    values = tuple(pattern)
    if len(values) >= 5:
        name, regex, category, description, priority = values[:5]
        return str(name), str(regex), str(category), str(description), int(priority)
    if len(values) == 2:
        name, regex = values
        return str(name), str(regex), "motif", "", 5
    name = str(values[0]) if values else "motif"
    return name, re.escape(name), "motif", "", 5


def _iter_motif_matches(pattern_regex, sequence_text):
    regex = re.compile(f"(?=({pattern_regex}))")
    for match in regex.finditer(sequence_text):
        matched_sequence = match.group(1)
        if not matched_sequence:
            continue
        start_idx = match.start(1)
        end_idx = start_idx + len(matched_sequence) - 1
        yield start_idx, end_idx, matched_sequence


def _motif_text_to_regex(text):
    token = str(text).strip().upper()
    token = token.replace("-", "")
    pieces = []
    index = 0
    while index < len(token):
        char = token[index]
        if char == "X":
            index += 1
            digits = []
            while index < len(token) and token[index].isdigit():
                digits.append(token[index])
                index += 1
            if digits:
                pieces.append(f".{{{int(''.join(digits))}}}")
            else:
                pieces.append(".")
            continue
        pieces.append(re.escape(char))
        index += 1
    return "".join(pieces)


def _fetch_uniprot_feature_payload(session, accession):
    cache = getattr(session, "_codex_bridge_uniprot_feature_cache", None)
    if cache is None:
        cache = {}
        session._codex_bridge_uniprot_feature_cache = cache
    if accession in cache:
        return cache[accession]

    url = f"https://rest.uniprot.org/uniprotkb/{accession}.json"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "ChimeraX-Codex-Bridge/0.1"})
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        payload = None
    cache[accession] = payload
    return payload


def _fetch_rcsb_entry_payload(session, entry_id):
    cache = getattr(session, "_codex_bridge_rcsb_entry_cache", None)
    if cache is None:
        cache = {}
        session._codex_bridge_rcsb_entry_cache = cache

    normalized = str(entry_id or "").strip().upper()
    if not normalized:
        return None
    if normalized in cache:
        return cache[normalized]

    url = f"https://data.rcsb.org/rest/v1/core/entry/{normalized}"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "ChimeraX-Codex-Bridge/0.1"})
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        payload = None
    cache[normalized] = payload
    return payload


def _rcsb_entry_summary(entry_id, payload, source="RCSB"):
    struct = payload.get("struct", {}) or {}
    info = payload.get("rcsb_entry_info", {}) or {}
    exptl = payload.get("exptl", []) or []
    keywords = payload.get("struct_keywords", {}) or {}

    methods = []
    for item in exptl:
        method = str((item or {}).get("method", "")).strip()
        if method and method not in methods:
            methods.append(method)
    resolution = info.get("resolution_combined", []) or []
    resolution_text = ", ".join(str(value) for value in resolution[:2]) if resolution else ""

    return {
        "entry_id": str(entry_id or "").upper(),
        "source": str(source or "RCSB"),
        "title": str(struct.get("title", "")).strip(),
        "methods": methods,
        "resolution": resolution_text,
        "keywords": str(keywords.get("pdbx_keywords", "")).strip(),
    }


def _format_rcsb_reference_line(entry):
    bits = [entry.get("entry_id", "(unknown)")]
    source = entry.get("source")
    if source:
        bits.append(f"source {source}")
    title = entry.get("title")
    if title:
        bits.append(title)
    methods = entry.get("methods") or []
    if methods:
        bits.append("method " + ", ".join(methods[:2]))
    resolution = entry.get("resolution")
    if resolution:
        bits.append(f"resolution {resolution} A")
    keywords = entry.get("keywords")
    if keywords:
        bits.append("keywords " + keywords)
    return "; ".join(bits)


def _guess_model_pdb_ids(model):
    texts = [
        getattr(model, "name", ""),
        getattr(model, "filename", ""),
        getattr(model, "path", ""),
    ]
    seen = []
    for text in texts:
        for entry_id in _extract_pdb_ids(text):
            if entry_id not in seen:
                seen.append(entry_id)
    return seen[:3]


def _extract_pdb_ids(text):
    hits = []
    for token in re.findall(r"\b([0-9][A-Za-z0-9]{3})\b", str(text or "")):
        upper = token.upper()
        if upper not in hits:
            hits.append(upper)
    return hits


def _iter_uniprot_features(payload):
    for feature in payload.get("features", []) or []:
        feature_type = _normalize_uniprot_feature_type(feature.get("type", ""))
        if feature_type is None:
            continue
        yield {
            "feature_type": feature_type,
            "description": str(feature.get("description", "") or "").strip(),
            "label": str(feature.get("description", "") or feature_type).strip(),
            "start": _feature_position_value(feature.get("location", {}), "start"),
            "end": _feature_position_value(feature.get("location", {}), "end"),
        }


def _normalize_uniprot_feature_type(feature_type):
    token = str(feature_type or "").strip()
    if not token:
        return None
    normalized = token.lower().replace("_", " ").replace("-", " ")
    if normalized in UNIPROT_FEATURE_TYPE_ALIASES:
        return UNIPROT_FEATURE_TYPE_ALIASES[normalized]
    for key, value in UNIPROT_FEATURE_TYPE_ALIASES.items():
        if key in normalized:
            return value
    return None


def _feature_position_value(location, key):
    boundary = (location or {}).get(key, {}) or {}
    value = boundary.get("value")
    try:
        return int(value)
    except Exception:
        return None


def _map_uniprot_feature_to_chain(feature, db_start, db_end, chain_start, chain_end):
    start = feature.get("start")
    end = feature.get("end")
    if start is None or end is None:
        return None
    if end < db_start or start > db_end:
        return None
    clipped_start = max(start, db_start)
    clipped_end = min(end, db_end)
    mapped_start = chain_start + (clipped_start - db_start)
    mapped_end = chain_start + (clipped_end - db_start)
    if mapped_start > chain_end or mapped_end < chain_start:
        return None
    mapped_start = max(mapped_start, chain_start)
    mapped_end = min(mapped_end, chain_end)
    return int(mapped_start), int(mapped_end)


def _chain_interface_report(model, cutoff):
    reports = []
    for item in get_complex_interfaces(model, contact_cutoff=cutoff):
        reports.append(
            f"interface {item['chain_a']}-{item['chain_b']}: min {item['min_distance']:.2f} A, "
            f"{item['contacts_a']}+{item['contacts_b']} contact-like residues"
        )
    reports.sort()
    return reports


def _top_lines(text, prefix="- ", limit=8):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return [prefix + line for line in lines[:limit]]


def _chunk_observation_lines(domains_text):
    lines = []
    for line in domains_text.splitlines():
        if "chunk" in line:
            lines.append("- " + line.strip())
    return lines[:8]


def _sequence_observation_lines(sequence_text):
    lines = []
    for line in sequence_text.splitlines():
        if "coverage" in line or "missing gaps" in line:
            lines.append("- " + line.strip())
    return lines[:8]


def _metal_hypothesis_lines(metal_text):
    lines = []
    if "direct coordinators:" in metal_text and "none within" not in metal_text:
        lines.append("- A metal-dependent catalytic or structural stabilization site is plausible.")
    if "HIS" in metal_text or "ASP" in metal_text or "GLU" in metal_text:
        lines.append("- Histidine/aspartate/glutamate residues near the metal are consistent with catalytic or coordination roles.")
    return lines


def _ligand_hypothesis_lines(ligand_text):
    lines = []
    if "ligand:" in ligand_text:
        lines.append("- Residues clustered around the ligand are plausible substrate-recognition or catalytic-pocket residues.")
    if "catalytic-like nearby:" in ligand_text and "none" not in ligand_text:
        lines.append("- Catalytic-like polar or charged side chains near the ligand support an enzyme active-site interpretation.")
    return lines


def _complex_hypothesis_lines(complex_text):
    lines = []
    if "interface" in complex_text and "contact-like residues" in complex_text:
        lines.append("- The assembly may rely on specific chain-chain interfaces rather than a monomer-only functional state.")
    return lines


def _domain_hypothesis_lines(domains_text):
    lines = []
    if "chunk" in domains_text:
        lines.append("- Long linkers or gap-separated chunks may indicate domain organization or flexible insertions.")
    return lines


def _motif_hypothesis_lines(motif_text):
    lines = []
    if "HExxH" in motif_text:
        lines.append("- An HExxH-like motif may indicate a metal-dependent catalytic signature.")
    if "CxxC" in motif_text or "CxxxC" in motif_text:
        lines.append("- Cys-rich motifs may support metal coordination or structural zinc-binding roles.")
    if "DxH" in motif_text or "HxH" in motif_text:
        lines.append("- Histidine/aspartate-rich motifs could mark catalytic or coordination hotspots.")
    return lines


def _metal_next_checks(metal_text):
    lines = []
    if "metal site:" in metal_text:
        lines.append("- Visualize the metal shell and inspect first-shell residues for conserved acidic/histidine side chains.")
        lines.append("- Compare the metal-binding chain to UniProt annotation and known metalloenzyme motifs.")
    return lines


def _ligand_next_checks(ligand_text):
    lines = []
    if "ligand:" in ligand_text:
        lines.append("- Compare ligand-adjacent residues with known catalytic motifs and mutational literature if available.")
        lines.append("- Visualize the ligand shell and check whether metal and ligand neighborhoods overlap.")
    return lines


def _complex_next_checks(complex_text):
    lines = []
    if "interface" in complex_text:
        lines.append("- Check whether interface residues cluster around the active-site region or form a separate oligomerization surface.")
    return lines


def _domain_next_checks(domains_text):
    lines = []
    if "chunk" in domains_text:
        lines.append("- Map chunk boundaries onto secondary-structure breaks and sequence gaps to test whether they reflect domain splits.")
    return lines


def _motif_next_checks(motif_text):
    lines = []
    if "No motif-like sequence patterns detected." in motif_text:
        return lines
    lines.append("- Highlight motif hits and compare their spatial positions against ligand, metal, and catalytic-candidate residues.")
    lines.append("- Test whether motif-bearing chains overlap with active-site-bearing or scaffold chains.")
    return lines


def get_complex_interfaces(model, contact_cutoff=8.0):
    details = []
    chain_entries = []
    for chain in model.chains:
        residues = chain.existing_residues
        pats = residues.existing_principal_atoms
        if len(pats) == 0:
            continue
        chain_entries.append((chain, pats.scene_coords))

    for i in range(len(chain_entries)):
        chain_a, coords_a = chain_entries[i]
        for j in range(i + 1, len(chain_entries)):
            chain_b, coords_b = chain_entries[j]
            if len(coords_a) == 0 or len(coords_b) == 0:
                continue
            d = np.linalg.norm(coords_a[:, None, :] - coords_b[None, :, :], axis=2)
            min_d = float(d.min())
            if min_d > contact_cutoff:
                continue
            details.append(
                {
                    "chain_a": str(chain_a.chain_id).strip() or "?",
                    "chain_b": str(chain_b.chain_id).strip() or "?",
                    "min_distance": min_d,
                    "contacts_a": int((d.min(axis=1) <= contact_cutoff).sum()),
                    "contacts_b": int((d.min(axis=0) <= contact_cutoff).sum()),
                    "chain_a_spec": chain_a.atomspec,
                    "chain_b_spec": chain_b.atomspec,
                }
            )
    details.sort(key=lambda item: (-(item["contacts_a"] + item["contacts_b"]), item["min_distance"]))
    return details


def best_interface_pair(session, model_hint=None):
    from chimerax.atomic import AtomicStructure

    selected_spec = resolve_model_spec(session, model_hint) if model_hint else None
    best = None
    for model in session.models.list(type=AtomicStructure):
        spec = f"#{getattr(model, 'id_string', '?')}"
        if selected_spec and spec != selected_spec:
            continue
        details = get_complex_interfaces(model)
        if not details:
            continue
        candidate = details[0]
        if best is None or (candidate["contacts_a"] + candidate["contacts_b"]) > (best["contacts_a"] + best["contacts_b"]):
            best = candidate
    return best


def best_metal_site(session, model_hint=None):
    sites = get_metal_sites(session, model_hint=model_hint)
    return sites[0] if sites else None


def best_ligand_site(session, model_hint=None):
    sites = get_ligand_sites(session, model_hint=model_hint)
    return sites[0] if sites else None


def _site_score(primary_count, secondary_count, closest_distance):
    score = 0
    score += min(primary_count * 2, 6)
    score += min(secondary_count, 3)
    if closest_distance and closest_distance <= 2.5:
        score += 2
    elif closest_distance and closest_distance <= 3.2:
        score += 1
    return score


def _residue_spec(model_spec, residue):
    chain_id = str(residue.chain_id).strip() or "?"
    return f"{model_spec}/{chain_id}:{int(residue.number)}"


def _site_residue_specs(model_spec, hits):
    return [f"{model_spec}/{hit['chain_id']}:{int(hit['number'])}" for hit in hits]


def _complex_role_lines(interface_details):
    if not interface_details:
        return []
    degree = Counter()
    for item in interface_details:
        degree[item["chain_a"]] += 1
        degree[item["chain_b"]] += 1
    max_degree = max(degree.values()) if degree else 0
    core = sorted([cid for cid, deg in degree.items() if deg == max_degree])
    peripheral = sorted([cid for cid, deg in degree.items() if deg < max_degree])
    lines = []
    if core:
        lines.append("likely core chains: " + ", ".join(core))
    if peripheral:
        lines.append("likely peripheral/accessory chains: " + ", ".join(peripheral))
    return lines


def _role_lines_for_model(session, model):
    spec = f"#{getattr(model, 'id_string', '?')}"
    interfaces = get_complex_interfaces(model)
    ligands = [site for site in get_ligand_sites(session, model_hint=spec)]
    metals = [site for site in get_metal_sites(session, model_hint=spec)]
    catalytic = [c for c in score_catalytic_residues(session, model_hint=spec)]

    ligand_chains = sorted({site["ligand_chain"] for site in ligands})
    metal_chains = sorted({site["metal_chain"] for site in metals})
    catalytic_chains = sorted({cand["chain_id"] for cand in catalytic[:10]})

    lines = []
    homo_groups = _complex_sequence_group_lines({
        "chains": [
            {
                "id": str(chain.chain_id).strip() or "?",
                "sequence_text": getattr(chain, "characters", "") or "",
            }
            for chain in model.chains
        ]
    })
    lines.extend(homo_groups)
    if ligand_chains:
        lines.append("ligand-bearing chains: " + ", ".join(ligand_chains))
    if metal_chains:
        lines.append("metal-bearing chains: " + ", ".join(metal_chains))
    if catalytic_chains:
        lines.append("top catalytic-candidate chains: " + ", ".join(catalytic_chains))
    lines.extend(_complex_role_lines(interfaces))
    return lines


def _consensus_label(reasons):
    joined = " ".join(reasons)
    if "metal direct coordination" in joined and "ligand-pocket catalytic-like" in joined:
        return "metal+ligand consensus"
    if "metal" in joined:
        return "metal-supported"
    if "ligand" in joined:
        return "ligand-supported"
    return "context-supported"


def _role_hypothesis_lines(roles_text):
    lines = []
    if "ligand-bearing chains:" in roles_text or "metal-bearing chains:" in roles_text:
        lines.append("- Active-site chemistry may be concentrated on a subset of chains rather than uniformly distributed across the assembly.")
    if "likely structural/scaffold chains:" in roles_text:
        lines.append("- Some chains may primarily stabilize assembly architecture while others carry catalytic chemistry.")
    return lines


def _role_next_checks(roles_text):
    lines = []
    if "top catalytic-candidate chains:" in roles_text:
        lines.append("- Compare catalytic-candidate chains against interface/core chains to separate chemistry from assembly support roles.")
    if "homo-oligomeric group:" in roles_text:
        lines.append("- Test whether repeated chains are symmetry-equivalent active subunits or only a subset carries ligand/metal.")
    return lines


def _complex_sequence_group_lines(model):
    groups = {}
    for chain in model["chains"]:
        seq = chain.get("sequence_text", "")
        if not seq:
            continue
        groups.setdefault(seq, []).append(chain["id"])
    lines = []
    for group in groups.values():
        if len(group) >= 2:
            lines.append("homo-oligomeric group: " + ", ".join(sorted(group)))
    return lines


def _complex_sequence_group_lines_for_structure(structure):
    pseudo_model = {
        "chains": [
            {
                "id": str(chain.chain_id).strip() or "?",
                "sequence_text": getattr(chain, "characters", "") or "",
            }
            for chain in structure.chains
        ]
    }
    return _complex_sequence_group_lines(pseudo_model)


def _complex_site_role_lines(session, model_spec, interface_details):
    ligand_sites = [site for site in get_ligand_sites(session) if site["model_spec"] == model_spec]
    metal_sites = [site for site in get_metal_sites(session) if site["model_spec"] == model_spec]

    ligand_chains = sorted({site["ligand_chain"] for site in ligand_sites})
    metal_chains = sorted({site["metal_chain"] for site in metal_sites})
    degree = Counter()
    for item in interface_details:
        degree[item["chain_a"]] += 1
        degree[item["chain_b"]] += 1
    lines = []
    if ligand_chains:
        lines.append("ligand-bearing chains: " + ", ".join(ligand_chains))
    if metal_chains:
        lines.append("metal-bearing chains: " + ", ".join(metal_chains))
    catalytic_like = sorted(set(ligand_chains) | set(metal_chains))
    if catalytic_like:
        lines.append("likely active-site-bearing chains: " + ", ".join(catalytic_like))
    if degree:
        max_degree = max(degree.values())
        structural = sorted([cid for cid, deg in degree.items() if deg == max_degree and cid not in catalytic_like])
        if structural:
            lines.append("likely structural/scaffold chains: " + ", ".join(structural))
    return lines


def _accumulate_residue_score(scored, model_spec, hit, weight, reason):
    residue_spec = f"{model_spec}/{hit['chain_id']}:{int(hit['number'])}"
    if residue_spec not in scored:
        scored[residue_spec] = {
            "model_spec": model_spec,
            "residue_spec": residue_spec,
            "chain_id": hit["chain_id"],
            "number": int(hit["number"]),
            "name": hit["name"],
            "score": 0,
            "reasons": set(),
        }
    bonus = _catalytic_residue_bonus(hit["name"], hit.get("min_distance", 0))
    scored[residue_spec]["score"] += weight + bonus
    scored[residue_spec]["reasons"].add(reason)


def _catalytic_residue_bonus(resname, distance):
    bonus = {
        "HIS": 2,
        "ASP": 2,
        "GLU": 2,
        "CYS": 2,
        "SER": 1,
        "TYR": 1,
        "LYS": 1,
        "ARG": 1,
    }.get(str(resname), 0)
    if distance and distance <= 2.6:
        bonus += 1
    return bonus


def _motif_supports_catalytic_assignment(motif):
    text = " ".join(
        str(motif.get(key, ""))
        for key in ("pattern_name", "category", "description")
    ).lower()
    return any(keyword in text for keyword in CATALYTIC_MOTIF_KEYWORDS)


def _motif_catalytic_candidate_hits(motif):
    hits = []
    sequence = str(motif.get("matched_sequence") or "").upper()
    specs = list(motif.get("residue_specs") or [])
    for aa, spec in zip(sequence, specs):
        if aa not in CATALYTIC_LIKE_ONE_LETTER:
            continue
        parsed = _parse_residue_focus_spec(spec)
        if parsed is None:
            continue
        model_spec, chain_id, number, _end = parsed
        if number is None:
            continue
        hits.append(
            {
                "model_spec": model_spec,
                "chain_id": chain_id,
                "number": int(number),
                "name": ONE_TO_THREE_RESNAME.get(aa, aa),
                "min_distance": 0,
                "atom_names": {"motif"},
            }
        )
    return hits


def _catalytic_cluster_candidate_hits(session, model_hint=None, cutoff=6.0, max_residues=800):
    from chimerax.atomic import AtomicStructure, Residue

    selected_spec = resolve_model_spec(session, model_hint) if model_hint else None
    hits = {}
    for model in session.models.list(type=AtomicStructure):
        model_spec = f"#{getattr(model, 'id_string', '?')}"
        if selected_spec and model_spec != selected_spec:
            continue
        residues = []
        coords = []
        for residue in model.residues:
            if str(getattr(residue, "name", "")).upper() not in CATALYTIC_LIKE_RESNAMES:
                continue
            chain_id = str(getattr(residue, "chain_id", "")).strip() or "?"
            polymer_type = getattr(residue, "polymer_type", None)
            if polymer_type is not None and polymer_type not in (Residue.PT_AMINO, Residue.PT_PROTEIN):
                continue
            residue_number = _safe_int(getattr(residue, "number", None), None)
            if residue_number is None:
                continue
            atoms = residue.atoms
            if len(atoms) == 0:
                continue
            heavy = atoms.filter(atoms.element_names != "H")
            if len(heavy) == 0:
                continue
            sidechain = heavy.filter(~np.isin(heavy.names, ["N", "CA", "C", "O", "OXT"]))
            use_atoms = sidechain if len(sidechain) else heavy
            residues.append(
                {
                    "model_spec": model_spec,
                    "chain_id": chain_id,
                    "number": residue_number,
                    "name": str(residue.name).upper(),
                    "atom_names": set(str(name) for name in use_atoms.names[:4]),
                }
            )
            coords.append(use_atoms.scene_coords.mean(axis=0))
            if len(residues) >= max_residues:
                break
        if len(residues) < 2:
            continue
        coords = np.asarray(coords)
        for index, residue in enumerate(residues[:-1]):
            deltas = coords[index + 1:] - coords[index]
            distances = np.linalg.norm(deltas, axis=1)
            close_indices = np.where(distances <= cutoff)[0]
            if len(close_indices) == 0:
                continue
            closest = float(distances[close_indices].min())
            _store_cluster_hit(hits, residue, closest)
            for offset in close_indices[:4]:
                _store_cluster_hit(hits, residues[index + 1 + int(offset)], float(distances[offset]))
    return list(hits.values())


def _store_cluster_hit(hits, residue, distance):
    key = (residue["model_spec"], residue["chain_id"], residue["number"])
    current = hits.get(key)
    if current is None:
        hits[key] = {
            **residue,
            "min_distance": float(distance),
        }
        return
    current["min_distance"] = min(float(current.get("min_distance", distance)), float(distance))


def _last_conservation_score_map(session):
    profile = getattr(session, "_codex_bridge_last_conservation_profile", None)
    if not profile:
        return {}
    mapping = {}
    for item in profile.get("residues", []) or []:
        spec = item.get("residue_spec")
        if spec:
            mapping[str(spec)] = int(item.get("grade", 0) or 0)
    return mapping


def _last_conservation_lines(session, model_hint=None, limit=6):
    profile = getattr(session, "_codex_bridge_last_conservation_profile", None)
    if not profile:
        return []
    selected_spec = resolve_model_spec(session, model_hint) if model_hint else None
    chain_spec = str(profile.get("target_chain_spec", ""))
    if selected_spec and chain_spec and not chain_spec.startswith(selected_spec + "/"):
        return []
    lines = [
        f"- {chain_spec} ({profile.get('sequence_count', 0)} sequences, alignment {profile.get('alignment_length', 0)})"
    ]
    for item in (profile.get("top_conserved") or [])[:limit]:
        lines.append(
            f"- {item['residue_spec']} {item['aa']} grade {item['grade']} "
            f"(identity {item['identity_fraction']:.2f})"
        )
    return lines


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default
