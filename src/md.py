"""OpenMM molecular dynamics for the AI -> Modeling -> MD toolbar action.

Key design decisions
====================

* **Periodic box + explicit water + ions** (not the previous vacuum setup).
  The earlier MD entry let the receptor and ligand drift out of contact
  because there was no periodic box and no solvent. Here we use
  ``Modeller.addSolvent`` with a configurable padding and 0.15 M NaCl, and
  ``app.PME`` electrostatics so the complex stays in its hydration shell.
* **NPT ensemble** (Langevin thermostat + MonteCarloBarostat) so the box
  size relaxes to physical density during equilibration.
* **AMBER ff14SB protein + TIP3P water + Joung/Cheatham ions**. Non-standard
  ligand residues are not (yet) parameterised automatically; if the target
  has a HETATM the user is warned and we abort cleanly rather than running
  a wrong simulation.
* The expensive work runs through the toolbar's existing background task
  runner so the ChimeraX UI stays responsive.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


_STANDARD_PROTEIN_RES = {
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
    "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
    # protonation variants AMBER ff14SB recognises
    "HID", "HIE", "HIP", "CYX", "CYM", "ASH", "GLH", "LYN",
}
_STANDARD_NUCLEIC_RES = {
    "A", "C", "G", "T", "U",
    "DA", "DC", "DG", "DT",
    "RA", "RC", "RG", "RU",
}
_STANDARD_SOLVENT_RES = {"HOH", "WAT", "TIP", "TIP3", "TP3", "NA", "CL", "K", "MG", "CA", "ZN"}


@dataclass
class MDOptions:
    target_spec: str
    padding_nm: float = 1.0
    ion_conc_M: float = 0.15
    temperature_K: float = 300.0
    timestep_fs: float = 2.0
    equilibration_steps: int = 50_000   # 100 ps at 2 fs
    production_steps: int = 500_000     # 1 ns at 2 fs
    report_interval: int = 5_000        # 10 ps stride
    output_dir: Path = field(default_factory=lambda: Path.home() / "codex_bridge_md")
    keep_input_ions: bool = True
    forcefield_xml: tuple = ("amber14-all.xml", "amber14/tip3pfb.xml")


def prompt_md_options(session, default_target_spec: Optional[str] = None) -> Optional[MDOptions]:
    """Modal dialog asking for the MD target and basic parameters."""
    try:
        from Qt.QtCore import Qt
        from Qt.QtWidgets import (
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QDoubleSpinBox,
            QFormLayout,
            QLabel,
            QSpinBox,
            QVBoxLayout,
        )
        from chimerax.atomic import AtomicStructure
    except Exception:
        return None

    targets = []
    try:
        for model in session.models.list(type=AtomicStructure):
            spec = f"#{model.id_string}"
            name = getattr(model, "name", "") or ""
            targets.append((f"{spec}  {name}".strip(), spec))
    except Exception:
        pass
    if not targets:
        try:
            session.logger.warning("MD: no atomic structures are open.")
        except Exception:
            pass
        return None

    parent = getattr(getattr(session, "ui", None), "main_window", None)
    dialog = QDialog(parent)
    dialog.setWindowTitle("MD setup")
    layout = QVBoxLayout(dialog)
    form = QFormLayout()

    target_combo = QComboBox(dialog)
    for label, spec in targets:
        target_combo.addItem(label, spec)
    if default_target_spec:
        idx = target_combo.findData(default_target_spec)
        if idx >= 0:
            target_combo.setCurrentIndex(idx)

    padding = QDoubleSpinBox(dialog)
    padding.setRange(0.5, 3.0)
    padding.setSingleStep(0.1)
    padding.setDecimals(1)
    padding.setValue(1.0)
    padding.setSuffix(" nm")

    ion_conc = QDoubleSpinBox(dialog)
    ion_conc.setRange(0.0, 1.0)
    ion_conc.setSingleStep(0.05)
    ion_conc.setDecimals(2)
    ion_conc.setValue(0.15)
    ion_conc.setSuffix(" M")

    temperature = QDoubleSpinBox(dialog)
    temperature.setRange(250.0, 400.0)
    temperature.setSingleStep(5.0)
    temperature.setDecimals(0)
    temperature.setValue(300.0)
    temperature.setSuffix(" K")

    equilibration = QSpinBox(dialog)
    equilibration.setRange(1_000, 1_000_000)
    equilibration.setSingleStep(5_000)
    equilibration.setValue(50_000)

    production = QSpinBox(dialog)
    production.setRange(10_000, 50_000_000)
    production.setSingleStep(50_000)
    production.setValue(500_000)

    form.addRow("Target", target_combo)
    form.addRow("Box padding", padding)
    form.addRow("Salt (NaCl)", ion_conc)
    form.addRow("Temperature", temperature)
    form.addRow("Equilibration steps (2 fs each)", equilibration)
    form.addRow("Production steps (2 fs each)", production)

    note = QLabel(
        "Solvated box + PBC + PME. Non-standard ligand residues are not\n"
        "parameterised automatically -- protein-only systems work out of\n"
        "the box; complexes with arbitrary ligands will abort with a\n"
        "clear message before any simulation runs.",
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
    return MDOptions(
        target_spec=str(target_combo.currentData() or ""),
        padding_nm=float(padding.value()),
        ion_conc_M=float(ion_conc.value()),
        temperature_K=float(temperature.value()),
        equilibration_steps=int(equilibration.value()),
        production_steps=int(production.value()),
    )


def _classify_residues(session, model_spec: str):
    """Return (protein_count, nucleic_count, solvent_count, nonstandard_residues).

    nonstandard_residues is a list of (resname, count) tuples. Anything not
    in the protein / nucleic / solvent allow-lists is treated as a ligand
    that the stock AMBER force field will not know how to parameterise.
    """
    from chimerax.atomic import AtomicStructure

    target_id = model_spec.lstrip("#")
    protein = nucleic = solvent = 0
    nonstandard: dict[str, int] = {}
    for model in session.models.list(type=AtomicStructure):
        if str(getattr(model, "id_string", "")) != target_id:
            continue
        for residue in getattr(model, "residues", ()) or ():
            name = str(getattr(residue, "name", "") or "").strip().upper()
            if name in _STANDARD_PROTEIN_RES:
                protein += 1
            elif name in _STANDARD_NUCLEIC_RES:
                nucleic += 1
            elif name in _STANDARD_SOLVENT_RES:
                solvent += 1
            else:
                nonstandard[name] = nonstandard.get(name, 0) + 1
    return protein, nucleic, solvent, sorted(nonstandard.items())


def run_md(session, options: MDOptions, executor=None):
    """Execute the MD pipeline. Designed to be called from a background task.

    Returns a dict describing the output files, or raises on hard errors.
    """
    from chimerax.core.commands import run as _cli_run

    def cli(cmd):
        if executor is not None:
            return executor(cmd)
        return _cli_run(session, cmd)

    try:
        import openmm as mm
        from openmm import app, unit
    except Exception as err:  # pragma: no cover - bundled with ChimeraX
        raise RuntimeError(f"OpenMM unavailable: {err}")

    workdir = Path(options.output_dir).expanduser()
    workdir.mkdir(parents=True, exist_ok=True)

    # Pre-flight: refuse to silently run on non-standard ligands.
    p, n, w, nonstandard = _classify_residues(session, options.target_spec)
    session.logger.info(
        f"MD: target {options.target_spec} -- protein={p}, nucleic={n}, "
        f"solvent/ions={w}, non-standard={[r for r,_ in nonstandard]}"
    )
    if nonstandard:
        msg = (
            f"MD aborted: target {options.target_spec} contains non-standard "
            f"residues that AMBER ff14SB cannot parameterise out of the box: "
            f"{', '.join(f'{r}x{c}' for r, c in nonstandard)}. "
            "Strip the ligand or extend the force field before retrying."
        )
        session.logger.warning(msg)
        raise RuntimeError(msg)

    # 1) Save target as PDB so OpenMM can read it. relbonds keeps CONECT records
    # for the few ions/HETATMs that survive _classify_residues.
    input_pdb = workdir / "input.pdb"
    cli(f'save "{input_pdb}" models {options.target_spec} format pdb')
    session.logger.info(f"MD: input -> {input_pdb}")

    # 2) Build the solvated, neutralised system.
    pdb = app.PDBFile(str(input_pdb))
    forcefield = app.ForceField(*options.forcefield_xml)
    modeller = app.Modeller(pdb.topology, pdb.positions)
    modeller.addHydrogens(forcefield, pH=7.0)
    modeller.addSolvent(
        forcefield,
        padding=options.padding_nm * unit.nanometer,
        ionicStrength=options.ion_conc_M * unit.molar,
        model="tip3p",
        positiveIon="Na+",
        negativeIon="Cl-",
    )
    n_atoms = modeller.topology.getNumAtoms()
    box = modeller.topology.getPeriodicBoxVectors()
    session.logger.info(
        f"MD: solvated. atoms={n_atoms}, "
        f"box=({box[0][0].value_in_unit(unit.nanometer):.2f}, "
        f"{box[1][1].value_in_unit(unit.nanometer):.2f}, "
        f"{box[2][2].value_in_unit(unit.nanometer):.2f}) nm"
    )

    # 3) System with PME + NPT (so the box adjusts to physical density).
    system = forcefield.createSystem(
        modeller.topology,
        nonbondedMethod=app.PME,
        nonbondedCutoff=1.0 * unit.nanometer,
        constraints=app.HBonds,
        rigidWater=True,
    )
    system.addForce(
        mm.MonteCarloBarostat(
            1.0 * unit.atmospheres,
            options.temperature_K * unit.kelvin,
            25,
        )
    )

    integrator = mm.LangevinMiddleIntegrator(
        options.temperature_K * unit.kelvin,
        1.0 / unit.picosecond,
        options.timestep_fs * unit.femtoseconds,
    )

    # Pick the fastest available OpenMM platform automatically.
    platform_name = _best_platform(mm)
    properties = {}
    if platform_name == "CUDA":
        properties["Precision"] = "mixed"
    elif platform_name == "OpenCL":
        properties["Precision"] = "mixed"
    platform = mm.Platform.getPlatformByName(platform_name)
    simulation = app.Simulation(
        modeller.topology, system, integrator, platform, properties
    )
    simulation.context.setPositions(modeller.positions)
    session.logger.info(f"MD: platform = {platform_name}")

    # 4) Minimisation + equilibration + production
    session.logger.info("MD: minimising...")
    simulation.minimizeEnergy()

    session.logger.info(
        f"MD: equilibrating {options.equilibration_steps} steps "
        f"({options.equilibration_steps * options.timestep_fs / 1000:.0f} ps)..."
    )
    simulation.step(options.equilibration_steps)

    # 5) Production trajectory + per-step state log.
    trajectory = workdir / "production.dcd"
    state_log = workdir / "state.csv"
    simulation.reporters.append(app.DCDReporter(str(trajectory), options.report_interval))
    simulation.reporters.append(
        app.StateDataReporter(
            str(state_log),
            options.report_interval,
            step=True,
            time=True,
            potentialEnergy=True,
            kineticEnergy=True,
            temperature=True,
            volume=True,
        )
    )
    session.logger.info(
        f"MD: production {options.production_steps} steps "
        f"({options.production_steps * options.timestep_fs / 1000:.0f} ps)..."
    )
    simulation.step(options.production_steps)

    # 6) Write the solvated topology so the DCD frames have something to bind
    # to in ChimeraX (the original PDB doesn't include the added waters/ions).
    topology_pdb = workdir / "solvated.pdb"
    final_state = simulation.context.getState(
        getPositions=True, enforcePeriodicBox=True
    )
    with open(topology_pdb, "w") as fh:
        app.PDBFile.writeFile(
            simulation.topology, final_state.getPositions(), fh, keepIds=True
        )
    session.logger.info(f"MD: topology -> {topology_pdb}")
    session.logger.info(f"MD: trajectory -> {trajectory}")

    # 7) Open the solvated structure + trajectory in ChimeraX.
    cli(f'open "{topology_pdb}"')
    cli(f'open "{trajectory}" structureModel #!last coordsets true')

    session.logger.info("MD: done.")
    return {
        "topology": str(topology_pdb),
        "trajectory": str(trajectory),
        "state_log": str(state_log),
    }


def _best_platform(mm) -> str:
    """Pick the fastest installed OpenMM platform: CUDA > OpenCL > CPU."""
    available = {mm.Platform.getPlatform(i).getName() for i in range(mm.Platform.getNumPlatforms())}
    for choice in ("CUDA", "OpenCL", "CPU"):
        if choice in available:
            return choice
    return "Reference"
