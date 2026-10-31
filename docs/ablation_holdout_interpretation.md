# FIX-19 / Step 110D — Holdout inference

Read-only analysis of 18 supplementary seeds (3001–20001) for each of S3/S4 × Hungarian/Greedy.
No pilot seeds used. FIX-19, original 640-run campaign, and holdout archives are unchanged.

## Energy effect (alpha=1 minus alpha=0)

| Scenario | Algorithm | Mean difference, Wh | Reduction vs alpha=0 | Unadjusted paired 95% CI, Wh | Exact sign-flip p | Holm p |
|---|---|---:|---:|---|---:|---:|
| S3 | hungarian | -143.80 | 2.44% | [-234.27, -53.33] | 0.00463 | 0.01852 |
| S3 | greedy | -143.01 | 2.42% | [-237.79, -48.23] | 0.00675 | 0.02026 |
| S4 | hungarian | -93.45 | 1.58% | [-191.29, +4.38] | 0.06028 | 0.06635 |
| S4 | greedy | -124.88 | 2.12% | [-237.19, -12.57] | 0.03317 | 0.06635 |

## Capped waiting and completion

| Scenario | Algorithm | Mean capped wait difference, s | 95% paired CI, s | Total completion difference |
|---|---|---:|---|---:|
| S3 | hungarian | +4.39 | [-1.20, +9.98] | +0 |
| S3 | greedy | +2.80 | [-5.63, +11.22] | +0 |
| S4 | hungarian | -0.38 | [-6.21, +5.45] | +1 |
| S4 | greedy | +1.41 | [-4.85, +7.67] | +0 |

## Limits

* The statistical unit is the scenario-specific seed, not individual passengers or repeated SOC variants.
* Paired sign-flip tests assume symmetric/exchangeable signs under the null; intervals assume independent seed-level differences.
* Only S3/S4, Hungarian/Greedy, 100% initial SOC and a single simulator calibration were tested.
* This tests the explicit assignment energy-cost weight while keeping battery feasibility checks on in both arms.
* This is simulated battery withdrawal, not external measured energy accuracy; grid charging is a separate outcome.
* A few individual seeds increase energy under alpha=1, and waiting times do not consistently improve.
* Neither the holdout nor the main campaign establishes hard real-time deadline guarantees.
* The pilot informed hypothesis and experiment selection; conclusions are scoped to these conditions.
