# FIX-24 / Step 117D — Battery-Constrained Assignment: Locked Paired Analysis

**Study:** Supplementary S5 (actual frozen CLI scenario S4 with 25% initial SOC), 12 vehicles, 100 requests per seed, eight algorithms × 20 paired seeds. The original 640 S1–S4 runs are unchanged.

**Independent archive qualification:** 160/160 verified; 14,982/16,000 completed (93.638%); 148,625 energy-blocked candidate-pair evaluations and 245,775 rejected insertions.
**Dispatch timing:** 389 / 10,750 complete dispatches exceeded 300 ms (3.619%). This is empirical soft-deadline evidence, not a hard real-time guarantee.

## Descriptive fulfillment and energy

| Algorithm | Completed / 2,000 | Fulfillment | Battery Wh/run | Grid Wh/run | Blocked pair evaluations | >300ms / dispatches |
|---|---:|---:|---:|---:|---:|---:|
| greedy | 1,963 | 98.15% | 5934.3 | 6264.7 | 6,288 | 7 / 1215 |
| hungarian | 1,960 | 98.00% | 6025.1 | 5979.0 | 6,710 | 5 / 1207 |
| mcmf | 1,968 | 98.40% | 6015.0 | 5985.8 | 6,121 | 4 / 1178 |
| auction | 1,961 | 98.05% | 6012.3 | 5975.6 | 6,810 | 11 / 1203 |
| random | 1,685 | 84.25% | 6412.8 | 4043.2 | 42,525 | 12 / 1660 |
| roundrobin | 1,562 | 78.10% | 6161.1 | 3400.7 | 64,082 | 99 / 1778 |
| greedy_minmax | 1,919 | 95.95% | 6113.0 | 6263.1 | 9,747 | 111 / 1314 |
| annealing | 1,964 | 98.20% | 6000.8 | 6005.1 | 6,342 | 140 / 1195 |

## Locked primary comparisons: algorithm minus Hungarian

Exact two-sided paired sign-flip tests over the 20 per-seed **completion-count differences**, Holm-adjusted across the seven comparisons specified in Step 117B. Positive values favor the named algorithm. Reported 95% Student-t intervals are unadjusted, based on 20 paired differences and do not provide simultaneous multiplicity coverage.

| Algorithm | Mean completed difference per 100 | Unadjusted 95% CI | Better / equal / worse seeds | Exact p | Holm p |
|---|---:|---|---:|---:|---:|
| greedy | +0.150 | [-0.989, +1.289] | 5/8/7 | 0.856934 | 1 |
| mcmf | +0.400 | [-0.040, +0.840] | 4/16/0 | 0.125 | 0.5 |
| auction | +0.050 | [-0.716, +0.816] | 4/15/1 | 1 | 1 |
| random | -13.750 | [-15.546, -11.954] | 0/0/20 | 1.90735e-06 | 1.33514e-05 |
| roundrobin | -19.900 | [-21.133, -18.667] | 0/0/20 | 1.90735e-06 | 1.33514e-05 |
| greedy_minmax | -2.050 | [-3.526, -0.574] | 4/3/13 | 0.0122528 | 0.061264 |
| annealing | +0.200 | [-0.088, +0.488] | 2/18/0 | 0.5 | 1 |

## Original S4 full-SOC context (descriptive only)

| Algorithm | Full-SOC S4 completed | S5 25%-SOC completed | Mean paired change in completions per seed |
|---|---:|---:|---:|
| greedy | 1999 | 1963 | -1.80 |
| hungarian | 2000 | 1960 | -2.00 |
| mcmf | 2000 | 1968 | -1.60 |
| auction | 2000 | 1961 | -1.95 |
| random | 1982 | 1685 | -14.85 |
| roundrobin | 1982 | 1562 | -21.00 |
| greedy_minmax | 1999 | 1919 | -4.00 |
| annealing | 2000 | 1964 | -1.80 |

## Interpretation for manuscript

The largest descriptive fulfillment was obtained by mcmf (1,968/2,000), followed by annealing (1,964/2,000). This is a descriptive ranking only; formal comparative conclusions must be based on the prespecified paired contrasts.

Energy-blocked pair evaluations refer to repeated candidate vehicle–request assessments, **not** distinct passengers refused service. Lower total battery withdrawal can result from serving fewer passengers; interpret gross battery withdrawal, grid imports, and completed requests jointly.

This protocol was locked **after** the exploratory two-seed SOC pilot, before the 160-run campaign. The 25% battery condition is an intentionally harsh simulation stress case, not an empirically validated operating distribution. The full-SOC S4 comparison changes initial battery conditions and was run with a different Linux kernel revision (7.0.0-38 vs 7.0.0-42); avoid claiming causal timing effects from that comparison.

The physical buggy was unavailable during the experiments. Reported battery and charging values remain model predictions, not measured or calibrated vehicle energy. Observed dispatch budget violations prevent a hard real-time execution guarantee.

## Reproducibility boundaries

- Frozen FIX-19 source SHA256: `7b52bb550414469937da3098f91313b366137d43bd48b8465b840a97bffeb0a0`
- Locked Step 117B protocol SHA256: `795b77c22a3b9a06142acf35271111afb7201fc2149e5a98483828b881696432`
- Validated Step 117C checkpoint SHA256: `50c1ccfebb7cc235166e85c33117783a4af84d8bdc497ed394ffa9598f42c9f5`
- The compact results ZIP indexes analyzed evidence; all 160 complete raw run archives should be preserved separately.
