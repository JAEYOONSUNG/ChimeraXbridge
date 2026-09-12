"""Run with plain Python; no Qt, ChimeraX session, network, or model files needed."""
import importlib.util
import colorsys
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "src" / "sequence_colors.py"
SPEC = importlib.util.spec_from_file_location("sequence_colors_check", SOURCE)
colors = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(colors)


def style(kind, letter="", name=""):
    return {"polymer_kind": kind, "letter": letter, "residue_name": name,
            "rgba": (21, 43, 65, 87)}


class SequenceColorChecks(unittest.TestCase):
    def test_all_standard_amino_acids_and_histidine(self):
        for letter in "ACDEFGHIKLMNPQRSTVWY":
            expected = ((143, 185, 232) if letter in "KR" else
                        (236, 164, 164) if letter in "DE" else
                        (233, 194, 127) if letter == "H" else (216, 220, 226))
            with self.subTest(letter=letter):
                result = colors.chemistry_color(style("protein", letter), charge=True)
                self.assertEqual(result["rgb"], expected)
                category = ("positive" if letter in "KR" else "negative" if letter in "DE" else
                            "histidine" if letter == "H" else "neutral")
                self.assertEqual(result["category"], category)
        histidine = colors.chemistry_color(style("protein", "H"), charge=True)
        self.assertIn("pH-dependent", histidine["label"])
        self.assertIn("pH-dependent", colors.CHARGE_TOOLTIP)

    def test_exact_base_colors_and_dna_names(self):
        expected = {"A": (185, 201, 189), "C": (176, 192, 207),
                    "G": (208, 199, 180), "T": (204, 186, 185),
                    "U": (194, 186, 202), "N": (183, 188, 196)}
        for base, rgb in expected.items():
            for name in (base, "D" + base):
                with self.subTest(base=base, name=name):
                    result = colors.chemistry_color(style("nucleic", "", name), bases=True)
                    self.assertEqual(result["rgb"], rgb)
                    self.assertEqual(result["category"], "base_" + base)

    def test_toggles_are_independent_even_for_shared_letters(self):
        for charge in (False, True):
            for bases in (False, True):
                protein = colors.chemistry_color(style("protein", "A"), charge=charge, bases=bases)
                nucleic = colors.chemistry_color(style("nucleic", "A"), charge=charge, bases=bases)
                self.assertEqual(protein is not None, charge)
                self.assertEqual(nucleic is not None, bases)
                if protein:
                    self.assertEqual(protein["rgb"], (216, 220, 226))
                if nucleic:
                    self.assertEqual(nucleic["rgb"], colors.BASE_COLORS["A"])

    def test_name_precedence_normalization_and_letter_fallback(self):
        lysine = colors.chemistry_color(style(" Protein ", "D", " lys "), charge=True)
        self.assertEqual(lysine["rgb"], (143, 185, 232))
        base = colors.chemistry_color(style("nucleic", "C", " da "), bases=True)
        self.assertEqual(base["rgb"], colors.BASE_COLORS["A"])
        modified = colors.chemistry_color(style("protein", "m", "MSE"), charge=True)
        self.assertEqual(modified["rgb"], (216, 220, 226))

    def test_unknown_symbols_are_explicitly_unassigned(self):
        for letter in ("X", "B", "Z", "?", "", "LYS"):
            result = colors.chemistry_color(style("protein", letter), charge=True)
            self.assertEqual(result["rgb"], (183, 188, 196))
            self.assertIn("unassigned", result["label"])
            self.assertEqual(result["category"], "unknown")
        for letter in ("N", "R", "Y", "X", "?", ""):
            result = colors.chemistry_color(style("nucleic", letter), bases=True)
            self.assertEqual(result["rgb"], (183, 188, 196))
            self.assertIn("ambiguous", result["label"])

    def test_unrelated_or_missing_polymer_kind_is_not_colored(self):
        for value in (None, {}, [], "protein", style("ligand", "K"), style("", "A")):
            self.assertIsNone(colors.chemistry_color(value, charge=True, bases=True))

    def test_structure_color_and_input_mapping_are_unchanged(self):
        original = style("protein", "K", "LYS")
        before = dict(original)
        result = colors.chemistry_color(original, charge=True, bases=True)
        self.assertEqual(original, before)
        self.assertNotIn("rgba", result)
        result["rgb"] = (0, 0, 0)
        self.assertEqual(colors.chemistry_color(original, charge=True)["rgb"], (143, 185, 232))

    def test_presets_are_quiet_and_have_meaningful_groups(self):
        palettes = {}
        for preset in ("muted", "purine_pyrimidine", "monochrome"):
            palettes[preset] = {
                base: colors.chemistry_color(style("nucleic", base), bases=True, base_palette=preset)["rgb"]
                for base in "ACGTUN"
            }
            for rgb in palettes[preset].values():
                saturation = colorsys.rgb_to_hsv(*(channel / 255 for channel in rgb))[1]
                self.assertLess(saturation, 0.2, (preset, rgb))
        self.assertEqual(len(set(palettes["muted"].values())), 6)
        grouped = palettes["purine_pyrimidine"]
        self.assertEqual(grouped["A"], grouped["G"])
        self.assertEqual(grouped["C"], grouped["T"])
        self.assertEqual(grouped["T"], grouped["U"])
        self.assertNotEqual(grouped["A"], grouped["C"])
        self.assertEqual(len(set(palettes["monochrome"].values())), 1)
        self.assertIs(colors.BASE_COLORS, colors.BASE_PALETTES["muted"])

    def test_presets_preserve_identity_scene_color_and_protein_classes(self):
        for preset in colors.BASE_PALETTES:
            for base in "ACGTUN":
                original = style("nucleic", base)
                before = dict(original)
                result = colors.chemistry_color(original, bases=True, base_palette=preset)
                self.assertEqual(result["category"], "base_" + base)
                self.assertEqual(original, before)
                self.assertIsNone(colors.chemistry_color(original, bases=False, base_palette=preset))
            self.assertEqual(colors.chemistry_color(style("protein", "K"), charge=True, base_palette=preset),
                             colors.chemistry_color(style("protein", "K"), charge=True))

    def test_palette_fallback_and_matching_legends(self):
        for invalid in (None, "old_preset", "", []):
            self.assertEqual(colors.normalize_base_palette(invalid), "muted")
            self.assertEqual(colors.chemistry_color(style("nucleic", "A"), bases=True, base_palette=invalid)["rgb"],
                             colors.BASE_COLORS["A"])
        self.assertIn("A/G purines", colors.base_legend_html("purine_pyrimidine"))
        self.assertIn("C/T/U pyrimidines", colors.base_legend_html("purine_pyrimidine"))
        self.assertIn("A/C/G/T/U/N", colors.base_legend_html("monochrome"))
        for preset, palette in colors.BASE_PALETTES.items():
            self.assertIn("#{:02X}{:02X}{:02X}".format(*palette["A"]), colors.base_legend_html(preset))


if __name__ == "__main__":
    result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(SequenceColorChecks))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("SEQUENCE_COLORS_OK")
