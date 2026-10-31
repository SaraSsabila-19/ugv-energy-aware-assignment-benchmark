# FIX-24 / Step 118: S5 Supplementary Reproducibility Release

Study scope: eight assignment algorithms, 20 paired seeds, 100 requests per seed,
12 UGVs. The **supplementary S5 label** means the original frozen S4 workload
with **initial battery SOC = 25%** and **reserve = 20%**. The physical vehicle was
not available for calibration. S5 stress assumptions are not field-validated.

## Verified result

- Validated 160/160 S5 run archives against source, workload, request outcomes,
  vehicle reserves, battery accounting, charging ledger and timing logs.
- Completed 14,982/16,000 offered trips; 148,625 energy-blocked **candidate
  evaluations**, not unique rejected passengers.
- Measured 389/10,750 dispatches above the 300 ms budget (3.62%).
- MCMF has the highest descriptive fulfillment (98.40%), without a statistically
  established improvement over Hungarian in the seven prespecified comparisons.
- Random and Round-Robin are significantly inferior to Hungarian on completed
  trips after Holm adjustment. Do not infer superiority for other algorithms.
- The post-pilot S5 analysis protocol was locked before the full 160-run study.
- Hardware/kernel difference from the original benchmark is documented.

## Contents

- `protocol/`: the original locked Step117B protocol and 160-run manifest.
- `analysis/step117d_shareable_analysis.zip`: primary results, comparisons,
  and primary-source provenance verified independently by this controller.
- `analysis/`: the three principal S5 analysis CSV tables and manuscript text.
- `archive_index_160.csv`: paths and content-tree SHA256 per validated S5 run.
- `archive_file_checksums_160.csv`: one SHA256 for each archived file, verified
  against stored experiment checkpoints when those hashes were available.
- `step118_release_summary.json`: counts, source identity and dependency hashes.

This compact evidence ZIP **does not contain all 160 raw run archives**. Keep
`research_revision_v2/step117c_s5_full160_campaign/` unchanged and retain the
original full 640-run archive separately. To reproduce, use the frozen FIX-19
simulator, the exact locked S5 manifest, and the recorded Python/dependency
versions, noting the documented Linux kernel revision.

The file SHA256 index `release_file_sha256.json` authenticates package members;
it is not an independent cryptographic signature. Do not report physical energy
accuracy or hard real-time guarantees based on these simulations.

Indexed raw archive files: **4,000**.
