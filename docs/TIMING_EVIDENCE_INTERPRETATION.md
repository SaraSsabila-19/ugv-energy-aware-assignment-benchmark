# Computational Timing Evidence and Interpretation

## Scope

This document concerns the accepted frozen S1-S4
primary benchmark: 640 simulation runs and 25,512
instrumented dispatch invocations.

The nominal dispatch-processing budget was 300 ms.

The measured results are empirical simulation timings,
not certified worst-case execution-time guarantees.

## Verified dispatch statistics

- Total dispatch invocations: 25,512
- Dispatch invocations exceeding 300 ms: 77
- Observed exceedance frequency: 0.3018%
- Maximum recorded dispatch duration: 2059.28 ms

## Measured phase attribution

The shares below were independently recalculated
from the five phase-duration totals recorded in
`verification/timing/step112a_latency_audit.json`.

For each scope:

Phase share (%) = 100 x phase-duration total /
sum of the five recorded phase-duration totals.

| Measured phase | All dispatches | Over-budget dispatches |
|---|---:|---:|
| Cost-matrix construction | 96.3671% | 98.5260% |
| Optimization solver | 2.1176% | 0.7514% |
| Cardinality diagnostics | 0.1550% | 0.0332% |
| Assignment activation | 0.8536% | 0.5103% |
| Other dispatch processing | 0.5067% | 0.1792% |
| **Total** | **100%** | **100%** |

The all-dispatch denominator is
1648957.933738 ms.

The over-budget denominator is
28416.064945 ms.

The 98.53% figure refers specifically to the summed
recorded phase durations of the 77 dispatches exceeding
300 ms. It does not describe every dispatch individually.

## Selected timing repeatability checks

The archived repeatability dataset contains four
selected cases with five reruns each.

| Selected case | Repeated target dispatches over 300 ms |
|---|---:|
| `S2_greedy_minmax_seed16001` | 0/5 |
| `S3_greedy_minmax_seed10001` | 5/5 |
| `S3_random_seed15001` | 0/5 |
| `S4_auction_seed15001` | 0/5 |

These selected reruns do not establish population-level
timing reliability or a complete worst-case bound.

Some original extreme timing outliers did not recur
under the selected rerun conditions. They remain part
of the accepted original benchmark.

## FIX-21 status

The separately evaluated geometry-cache candidate
failed the critical deadline-margin acceptance gate
and was not promoted to the accepted main benchmark.

The frozen primary results must not be replaced
with outcomes from that candidate.

## Publication-ready Results wording

Across the 640 accepted simulation runs, 25,512
instrumented dispatch invocations were recorded,
of which 77 (0.302%) exceeded the nominal 300 ms
processing budget. Cost-matrix construction accounted
for 96.37% of summed recorded dispatch-phase durations
across all dispatches and 98.53% among the 77
over-budget invocations. Solver execution accounted
for 0.75% of the summed measured phase durations
among over-budget invocations. The observed maximum
dispatch duration was 2059.28 ms.

## Publication-ready limitations wording

The 300 ms processing target was not guaranteed.
The observed exceedances and selected rerun behavior
preclude a hard real-time execution claim. Recorded
dispatch processing excludes sensing, communication,
actuation, complete closed-loop control, and any
unmeasured deployment-level scheduling interference.
Timing measurements are environment-dependent, and
dispatch invocations within the same simulation run
should not be treated as independent statistical trials.

The phase attribution is reconstructed from archived
aggregate measurements, not independently recalculated
from every original raw timing event.

## Evidence traceability

- `verification/timing/step112a_latency_audit.json`
- `verification/timing/step113d_decision.json`
- `verification/timing/step114_timing_qualification.json`
- `data/timing/step112c_repeatability_results.csv`
- `data/primary/step114_scenario_timing.csv`
- `data/primary/run_level_640.csv`

The accepted frozen simulator and original experiments
were not modified for this analysis.
