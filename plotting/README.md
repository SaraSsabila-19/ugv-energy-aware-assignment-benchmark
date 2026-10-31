# Reproducing the UGV benchmark figures

This directory provides a portable route to regenerate
the seven main and supplementary benchmark figure groups
from the existing released research datasets.

## Contents

- make_figures.py: unchanged original scientific plotting code
- reproduce_from_release.py: portable input-staging adapter
- requirements.txt: plotting package requirements
- original_source_manifest.json: original figure-kit input checksums
- Figure_Placement_Guide.md: manuscript placement guidance

## Running

Create a Python environment with numpy, pandas and
matplotlib, using the versions or constraints in
plotting/requirements.txt.

From the root of the repository, run:

    python3 plotting/reproduce_from_release.py --output ~/ugv-figures-output

The output directory must not already exist and must
be outside the Git repository.

The program first runs the repository data verifier.
It then stages the nine existing released datasets in a
temporary folder, generates an updated input-checksum
manifest, executes the unchanged figure generator and
copies 21 validated files into the output directory.

The temporary staging folder is automatically removed.

## Data provenance

Eight of the nine figure inputs are byte-identical to
the original figure-kit evidence.

The released primary run-level CSV contains verified
relative archive paths instead of the original absolute
archive paths. Numerical plot inputs are unchanged.

The original source-checksum manifest is preserved for
comparison. The adapter validates the eight exact
inputs against it and verifies the primary data using
the repository's accepted summary-data verifier.

No duplicate research CSV or JSON data are committed
in this plotting directory.

## Scope and limitations

This regenerates figures from accepted derived evidence.
It does not rerun UGV simulations or reconstruct raw
simulation events.

Figure 1 uses mean picked-up response waiting time,
not the deadline-limited waiting-time metric.

The ablation confidence intervals are unadjusted,
although the indicated energy-significance decisions
use Holm-corrected p-values.

Plot appearance can differ slightly across environments
because of fonts and library versions.

The research software and datasets retain their
separate, unresolved licensing considerations.
