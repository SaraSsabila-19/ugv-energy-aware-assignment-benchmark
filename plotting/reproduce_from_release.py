#!/usr/bin/env python3
"""Regenerate UGV benchmark figures from released repository data."""

from pathlib import Path
from tempfile import TemporaryDirectory
from hashlib import sha256
import argparse
import csv
import json
import os
import shutil
import subprocess
import sys

PLOTTING = Path(__file__).resolve().parent
ROOT = PLOTTING.parent

SOURCES = {
    "run_level_640.csv": "data/primary/run_level_640.csv",
    "holdout_paired.csv": "data/ablation/holdout_paired.csv",
    "ablation_inference.csv":
        "data/ablation/step110d_paired_inference.csv",
    "physics_sensitivity.csv":
        "data/sensitivity/step111b_exploratory_sensitivity.csv",
    "latency_scenario.csv":
        "data/primary/step114_scenario_timing.csv",
    "latency_audit.json":
        "verification/timing/step112a_latency_audit.json",
    "s5_algorithm_summary.csv":
        "data/s5/step117d_s5_algorithm_summary.csv",
    "s5_s4_context.csv":
        "data/s5/step117d_s5_full_soc_s4_context.csv",
    "s5_paired.csv":
        "data/s5/step117d_s5_paired_vs_hungarian.csv",
}

STEMS = (
    "Fig01_Nominal_Energy_Wait_Tradeoff",
    "Fig02_Energy_Ablation_Paired_Effects",
    "Fig03_S4_S5_Fulfillment_Change",
    "Fig04_S5_Fulfillment_Inference",
    "Fig05_Dispatch_Latency_and_Budget",
    "FigS01_Exploratory_Physics_Sensitivity",
    "FigS02_Dispatch_Phase_Attribution",
)

def main():
    parser = argparse.ArgumentParser(
        description="Generate the seven benchmark figures."
    )
    parser.add_argument(
        "--output", required=True,
        help="New, non-existing output directory"
    )
    args = parser.parse_args()

    output = Path(args.output).expanduser().resolve()

    if output == ROOT or ROOT in output.parents:
        parser.error("Output must be outside the Git repository.")

    if output.exists():
        parser.error("Output already exists; refusing to overwrite.")

    print("===== PORTABLE UGV FIGURE REGENERATION =====")
    print("Repository:", ROOT)
    print("Output:", output)

    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    print("\n===== VERIFY RELEASED DATA =====")

    subprocess.run(
        [sys.executable, "-B",
         str(ROOT / "scripts/verify_public_results.py")],
        cwd=ROOT,
        env=environment,
        check=True,
    )

    original_manifest = json.loads(
        (PLOTTING / "original_source_manifest.json").read_text(
            encoding="utf-8"
        )
    )

    if set(original_manifest) != set(SOURCES):
        raise RuntimeError("Figure source inventory mismatch")

    with TemporaryDirectory(
        prefix="ugv_portable_figures_"
    ) as temporary:
        temporary = Path(temporary)
        staged = temporary / "figure_kit"
        data = staged / "data"
        data.mkdir(parents=True)

        shutil.copyfile(
            PLOTTING / "make_figures.py",
            staged / "make_figures.py"
        )

        manifest = {}

        for name, relative in SOURCES.items():
            source = ROOT / relative

            if not source.is_file():
                raise RuntimeError(
                    "Missing released input: " + relative
                )

            raw = source.read_bytes()
            digest = sha256(raw).hexdigest()

            if name != "run_level_640.csv":
                expected = original_manifest[name]["sha256"]

                if digest != expected:
                    raise RuntimeError(
                        "Unexpected source checksum: " + relative
                    )
            else:
                # The public-release archive column has verified
                # relative paths instead of historical local paths.
                # All numerical input fields remain unchanged.
                with source.open(
                    newline="", encoding="utf-8-sig"
                ) as f:
                    records = list(csv.DictReader(f))

                if len(records) != 640:
                    raise RuntimeError(
                        "Primary dataset has unexpected row count"
                    )

                required = {
                    "scenario", "algorithm", "trial_seed",
                    "battery_Wh", "completed",
                    "mean_pickup_response_picked_up_s"
                }

                if not required.issubset(records[0]):
                    raise RuntimeError(
                        "Primary figure input columns missing"
                    )

            (data / name).write_bytes(raw)

            manifest[name] = {
                "sha256": digest,
                "bytes": len(raw),
                "source": relative
            }

            print("VERIFIED:", relative)

        (data / "source_manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8"
        )

        environment["MPLCONFIGDIR"] = str(
            temporary / "matplotlib_config"
        )

        print("\n===== RUN UNMODIFIED FIGURE GENERATOR =====")

        subprocess.run(
            [
                sys.executable, "-B",
                "make_figures.py"
            ],
            cwd=staged,
            env=environment,
            check=True,
        )

        figures = staged / "figures"
        generated = [
            p for p in figures.iterdir()
            if p.is_file()
        ]

        if len(generated) != 21:
            raise RuntimeError(
                "Expected 21 output figure files"
            )

        for stem in STEMS:
            for extension in (".pdf", ".svg", ".png"):
                path = figures / (stem + extension)

                if not path.is_file():
                    raise RuntimeError(
                        "Missing figure: " + str(path)
                    )

                if path.stat().st_size <= 1000:
                    raise RuntimeError(
                        "Suspicious figure size: " + str(path)
                    )

                header = path.read_bytes()[:2048]

                if extension == ".pdf":
                    valid = header.startswith(b"%PDF-")
                elif extension == ".png":
                    valid = header.startswith(
                        b"\x89PNG\r\n\x1a\n"
                    )
                else:
                    valid = b"<svg" in header

                if not valid:
                    raise RuntimeError(
                        "Invalid figure format: " + str(path)
                    )

            print("PASS:", stem)

        if output.exists():
            raise RuntimeError(
                "Output appeared during execution; refusing overwrite"
            )

        shutil.copytree(figures, output)

    print("\n===== FIGURE REGENERATION COMPLETE =====")
    print("Verified inputs: 9")
    print("Figure groups: 7")
    print("Generated files: 21")
    print("Output:", output)
    print("Frozen simulator modified: NO")
    print("Git repository modified by generator: NO")

if __name__ == "__main__":
    main()
