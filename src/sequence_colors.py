"""Qt-free chemistry colors for sequence interiors, independent of scene colors.

These are categorical side-chain/base colors, not calculated protonation states.
The caller keeps the original ``style["rgba"]`` for the structure-color outline.
"""
from collections.abc import Mapping


CHARGE_COLORS = {
    "positive": (143, 185, 232),  # #8FB9E8
    "negative": (236, 164, 164),  # #ECA4A4
    "histidine": (233, 194, 127),  # #E9C27F
    "neutral": (216, 220, 226),  # #D8DCE2
    "unknown": (183, 188, 196),  # #B7BCC4
}
BASE_PALETTES = {
    "muted": {
        "A": (185, 201, 189),  # Sage
        "C": (176, 192, 207),  # Blue gray
        "G": (208, 199, 180),  # Sand
        "T": (204, 186, 185),  # Dusty rose
        "U": (194, 186, 202),  # Mauve gray
        "N": (183, 188, 196),
    },
    "purine_pyrimidine": {
        "A": (178, 195, 204), "G": (178, 195, 204),
        "C": (208, 197, 179), "T": (208, 197, 179), "U": (208, 197, 179),
        "N": (183, 188, 196),
    },
    "monochrome": dict.fromkeys("ACGTUN", (194, 201, 205)),
}
BASE_PALETTE_LABELS = {
    "muted": "Muted bases",
    "purine_pyrimidine": "Purine / pyrimidine",
    "monochrome": "Monochrome",
}
# Preserve the original public constant for callers using the default palette.
BASE_COLORS = BASE_PALETTES["muted"]

CHARGE_TOOLTIP = (
    "Amino-acid side-chain classes near neutral pH: K/R positive; D/E negative; "
    "H (histidine) pH-dependent; other standard amino acids usually uncharged. "
    "Unknown amino acids are gray and have no assigned charge. "
    "This is a sequence category, not a protonation calculation. "
    "The outline retains the structure color."
)
BASE_TOOLTIP = (
    "DNA/RNA bases: A adenine, C cytosine, G guanine, T thymine, U uracil. "
    "Choose a color preset using the dropdown arrow. "
    "Unknown or ambiguous bases are labeled N. "
    "The outline retains the structure color."
)

_PROTEIN_NAMES = dict(zip(
    ("ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
     "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL"),
    "ARNDCQEGHILKMFPSTWYV",
))
_PROTEIN_LETTERS = frozenset(_PROTEIN_NAMES.values())
_BASE_NAMES = {
    "A": "A", "DA": "A", "ADE": "A",
    "C": "C", "DC": "C", "CYT": "C",
    "G": "G", "DG": "G", "GUA": "G",
    "T": "T", "DT": "T", "THY": "T",
    "U": "U", "DU": "U", "URA": "U",
    "N": "N", "DN": "N",
}
_BASE_LABELS = {
    "A": "Adenine (A)", "C": "Cytosine (C)", "G": "Guanine (G)",
    "T": "Thymine (T)", "U": "Uracil (U)",
    "N": "Unknown or ambiguous base (N)",
}


def _legend_html(items, colors):
    return " &nbsp; ".join(
        '<span style="background-color: #{:02X}{:02X}{:02X}; color: #17212B">&nbsp;{}&nbsp;</span>'.format(*colors[key], label)
        for key, label in items
    )


CHARGE_LEGEND_HTML = _legend_html(
    (("positive", "K/R +"), ("negative", "D/E −"), ("histidine", "H +/0"),
     ("neutral", "neutral"), ("unknown", "X ?")),
    CHARGE_COLORS,
)
def normalize_base_palette(base_palette):
    """Use the muted preset if an older preference has an unknown value."""
    return base_palette if isinstance(base_palette, str) and base_palette in BASE_PALETTES else "muted"


def base_legend_html(base_palette="muted"):
    base_palette = normalize_base_palette(base_palette)
    colors = BASE_PALETTES[base_palette]
    if base_palette == "purine_pyrimidine":
        items = (("A", "A/G purines"), ("C", "C/T/U pyrimidines"), ("N", "N"))
    elif base_palette == "monochrome":
        items = (("A", "A/C/G/T/U/N"),)
    else:
        items = ((base, base) for base in "ACGTUN")
    return _legend_html(items, colors)


BASE_LEGEND_HTML = base_legend_html()


def chemistry_color(style, *, charge=False, bases=False, base_palette="muted"):
    """Return interior ``rgb``, ``label`` and ``category``, or ``None``.

    ``polymer_kind`` must be ``protein`` or ``nucleic`` and its corresponding
    toggle must be enabled. Canonical residue names take precedence over the
    one-letter code; the latter is a fallback for modified or unnamed residues.
    Protein categories are positive/negative/histidine/neutral/unknown; base
    categories are base_A/base_C/base_G/base_T/base_U/base_N. Unknown symbols
    receive an explicit gray classification. The input mapping
    and its original ``rgba`` are never changed.
    """
    if not isinstance(style, Mapping):
        return None
    kind = str(style.get("polymer_kind") or "").strip().lower()
    name = str(style.get("residue_name") or "").strip().upper()
    letter = str(style.get("letter") or "").strip().upper()
    if kind == "protein" and charge:
        letter = _PROTEIN_NAMES.get(name, letter)
        if letter in ("K", "R"):
            category, label = "positive", "Positive side chain (K/R)"
        elif letter in ("D", "E"):
            category, label = "negative", "Negative side chain (D/E)"
        elif letter == "H":
            category, label = "histidine", "Histidine (H): pH-dependent charge"
        elif letter in _PROTEIN_LETTERS:
            category, label = "neutral", "Usually uncharged side chain"
        else:
            category, label = "unknown", "Unknown amino acid (X): charge unassigned"
        return {"rgb": CHARGE_COLORS[category], "label": label, "category": category}
    if kind == "nucleic" and bases:
        colors = BASE_PALETTES[normalize_base_palette(base_palette)]
        letter = _BASE_NAMES.get(name, letter)
        if letter not in colors:
            letter = "N"
        return {"rgb": colors[letter], "label": _BASE_LABELS[letter], "category": "base_" + letter}
    return None
