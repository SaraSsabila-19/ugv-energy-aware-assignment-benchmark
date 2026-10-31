# FIX-19 / Step 115: Publication Reproducibility Evidence

## Status
- Accepted benchmark: frozen **FIX-19** source; 640 unique experiment runs, 4 scenarios × 8 algorithms × 20 paired seeds.
- Frozen experiment archives are **NOT** rewritten by this packaging step.
- The two FIX-21 candidates remain **experimental and unpromoted**; they are NOT part of the original results.
- The original 300 ms budget was exceeded 77 times in 25,512 measured dispatch invocations (0.302%). This is empirical soft-deadline performance, not a hard real-time guarantee.
- Energy-weighted ablation provides conditional simulated energy-savings evidence; it is not physical battery-model validation.
- Sensitivity testing documents uncertain physical parameters but does not establish empirical vehicle calibration.

## Contents
- `freeze/`: frozen source package (if under size cap), manifest, hashes and environment metadata.
- `campaign/`: original 640-run progress report with archive locations and checksums.
- `analysis/`: independent run-level comparisons and summary statistics.
- `ablation/`: 18-seed holdout comparisons and corrected exploratory inference.
- `physics/`: illustrative parameter-sensitivity summaries.
- `latency/`: timing qualification and original FIX-21 rejection evidence.
- `archive_index_640.csv`: one row per accepted experiment; paths are relative to `research_revision_v2`.
- `archive_file_checksums.csv`: SHA256 for each validated run file.
- `paired_workload_hashes_80.csv`: exact workload identities across algorithms.

## Reconstructing the complete research archive
This ZIP is a **compact evidence/index package** and does not contain all 640 complete raw simulation directories.
Keep the original run archives at their recorded paths, or copy them independently, preserving the directory names and hashes in `archive_file_checksums.csv`. Complete raw-event reproducibility requires those archives in addition to the compact ZIP.

## Scientific limits
The 640-run benchmark is simulation-based, at nominal 100% starting SOC and energy-weighted cost. The supplementary alpha=0/1 comparison tests the energy-cost contribution in selected S3/S4 Hungarian and Greedy cases. Actual vehicle power measurements were not supplied. Dispatch timing does not include sensing, communications, actuator execution, or system-wide worst-case latency.

## Integrity
Check the `release_file_sha256.json` document after extraction and the recorded SHA256 values before using or publishing a dataset. Do not replace the original accepted source with a candidate optimization.
