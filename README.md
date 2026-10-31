# A Reproducible Energy-Aware Benchmarking Framework for Real-Time Assignment in Autonomous UGV Fleets

Research reproducibility materials for the accompanying journal manuscript.

## Scope

Eight existing UGV assignment algorithms are benchmarked under
controlled simulation conditions.

- Primary benchmark S1-S4: 640 runs
- Battery-stress S5: 160 runs
- Energy-weighting ablation: 144 runs
- Exploratory physics sensitivity: 56 runs

Total: 1,000 analyzed simulation runs.

## Simulator

The accepted FIX-19 implementation is located at:

`src/frozen/app.py`

Its SHA-256 identity is retained in the verification records.

## Data

Run-level outputs, experimental manifests, statistical results,
archive indexes, and checksum inventories are included.

The `archive` fields in the two public run-level CSV files
use paths relative to the original `research_revision_v2`
directory. Their identities were validated against the
original archive indexes.

## Verification and limitations

See `docs/PUBLIC_RELEASE_NOTES.md`.

The complete raw simulation archives and original compact
evidence ZIP files are not included in this repository.

The original recorded checksums refer to the original files,
not to privacy-normalized derivatives.

The study is simulation-only. Physical UGV energy
calibration and external replication are not established.

Some scripts retain dependencies on the original project
layout and complete raw-run archives. This repository is
not yet certified as a turnkey independent reproduction.

## Data availability

The permanent location for the complete raw archives
and the journal release DOI remain to be finalized.

## License and citation

License and final manuscript citation metadata are pending
author and third-party data permission review.
