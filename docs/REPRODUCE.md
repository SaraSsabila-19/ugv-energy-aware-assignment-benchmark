# Reproducing the UGV Benchmark Evidence

## Study

A Reproducible Energy-Aware Benchmarking Framework for
Real-Time Assignment in Autonomous UGV Fleets.

This is a simulation-based comparison of eight existing
assignment algorithms, not a new algorithm proposal.

## Experimental scope

| Campaign | Runs |
|---|---:|
| Primary S1-S4 | 640 |
| Battery-stress S5 | 160 |
| Energy-weighting ablation | 144 |
| Exploratory physics sensitivity | 56 |
| Total | 1,000 |

## 1. Verify the published datasets

From the repository root, run:

    python3 scripts/verify_public_results.py

This requires only Python's standard library.

The checker verifies the accepted simulator SHA-256,
primary and S5 manifest identities, archive indexes,
run counts, workload matching, and selected published
completion and dispatch statistics.

It does not rerun the original simulations or validate
every raw event.

## 2. Accepted implementation

Frozen simulator:

    src/frozen/app.py

Expected SHA-256:

    7b52bb550414469937da3098f91313b366137d43bd48b8465b840a97bffeb0a0

The original road graph and demand hotspots are in inputs/.

Primary controller:

    scripts/campaigns/step108_full640_controller.py

Its source is identified from the research workflow, but
the original checkpoint does not record its controller
SHA-256.

Authenticated S5 controller:

    scripts/campaigns/step117c_s5_full160_campaign_kernelguard_v2.py

Its SHA-256 matches the accepted S5 checkpoint.

## 3. Recorded environment

The primary environment records CPython 3.14.4,
Linux x86-64, and kernel 7.0.0-38-generic.

The S5 execution used kernel 7.0.0-42-generic.
Therefore, timing results across environments should
not be treated as hardware-independent measurements.

The 64 recorded package versions are in requirements.txt.
This is an environment snapshot, not a tested portable
dependency lockfile.

Clean-environment installation and execution remain to
be independently validated.

## 4. Experimental evidence

Primary results:

    data/primary/
    config/primary/
    verification/primary/

S5 results:

    data/s5/
    config/s5/
    verification/s5/

Energy-weighting ablation:

    data/ablation/

Exploratory physics sensitivity:

    data/sensitivity/

Computational timing:

    data/timing/
    verification/timing/

Original analysis programs:

    analysis/

Original campaign and experiment scripts:

    scripts/

The recorded analysis scripts are archived unchanged.
Some retain assumptions about original directory layouts.
A portable, end-to-end analysis workflow has not yet
been independently demonstrated.

## 5. Statistical evaluation

The primary benchmark uses 20 paired seeds per
scenario and algorithm.

Energy-weighting ablation inference uses 18 non-pilot
paired seeds, with four Holm-adjusted comparisons.

S5 inference uses 20 paired seeds, with seven
Holm-adjusted comparisons against Hungarian.

The reported confidence intervals are unadjusted
paired Student-t intervals. Exact sign-flip testing
is used for the specified paired hypothesis tests.

## 6. Archive integrity and availability

The public run-level CSV archive fields have been
converted to verified paths relative to the original
research_revision_v2 directory.

Original and transformed file checksums are recorded in:

    verification/public_file_derivations.csv

Original archive checksum inventories are retained.
They do not mean that the complete raw archives are
present in this GitHub collection.

The complete raw-run directories and original compact
evidence packages are preserved separately.

A permanent public raw-data location and DOI have not
yet been finalized.

## 7. Scientific limitations

This study is simulation-only.

The vehicle battery-energy model has not been physically
validated against operating UGV measurements.

The 300 ms computational budget is an empirical timing
target, not a hard real-time guarantee.

The modeled battery reserve should not be interpreted
as a universally enforced physical BMS cutoff.

Successful data verification does not establish
independent external replication.

## 8. Additional requirements for full reproduction

Full execution requires the original configuration,
supported runtime dependencies, route-network and
hotspot files, controller procedures, and access to
the relevant raw-run archives.

Review the controller code and its output-directory
assumptions before launching any experimental campaign.
Do not overwrite the accepted historical archives.


## Timing-evidence interpretation

See [Computational Timing Evidence and Interpretation](TIMING_EVIDENCE_INTERPRETATION.md) for the independently recalculated phase attribution, repeatability limitations, and qualified manuscript wording.
