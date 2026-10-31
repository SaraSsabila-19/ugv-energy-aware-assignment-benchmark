# FIX-19 / Step 114: Timing qualification for manuscript

## Publication-ready Results text

Across 640 simulation runs (80 scenario–seed workload sets, each evaluated using eight assignment algorithms), 25,512 instrumented dispatch invocations were measured under a nominal 300 ms dispatch-processing budget. A total of 77 invocations (0.302%) exceeded this threshold. The maximum recorded dispatch duration was 2059.28 ms. Among the budget-exceeding invocations, cost-matrix construction accounted for 98.53% of the summed measured dispatch wall-clock duration; solver execution was a comparatively small contributor. These observations characterize the tested Python-based simulation environment, not a verified worst-case execution-time bound.

| Scenario | Dispatches | >300 ms | Miss rate | p99 (ms) | Max (ms) |
|---|---:|---:|---:|---:|---:|
| S1 | 3,840 | 0 | 0.000% | 84.28 | 148.54 |
| S2 | 5,600 | 1 | 0.018% | 135.87 | 422.44 |
| S3 | 7,920 | 35 | 0.442% | 269.04 | 2059.28 |
| S4 | 8,152 | 41 | 0.503% | 270.94 | 459.00 |

## Suggested limitations text

The 300 ms threshold was not guaranteed: the original benchmark recorded 77 violations. Selected unprofiled reruns confirmed a repeatable budget overrun for the S3 Greedy Min-Max workload, while several extreme original outliers did not recur. A separate FIX-21 geometry-cache candidate preserved deterministic outcomes in selected tests but failed the deadline-margin acceptance gate and was not promoted. Instrumented dispatch times exclude sensing, communications, actuation, end-to-end closed-loop control, and deployment-level worst-case scheduling interference. Accordingly, this work evaluates *real-time-oriented algorithmic dispatch in simulation*, not certified hard real-time operation.

## Reporting boundaries

- All percentages are empirical frequencies, not guaranteed failure probabilities.
- Dispatches within the same run are correlated; do not treat all dispatch invocations as independent statistical trials.
- Original outliers and budget violations remain included in the benchmark.
- FIX-21 tests are supplementary, not part of the frozen 640-run comparison.
- Hardware energy-model accuracy was not validated by these timing tests.
- Main source and all original experiment archives were not modified.
