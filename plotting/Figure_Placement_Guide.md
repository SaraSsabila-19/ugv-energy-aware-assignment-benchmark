# Figure placement guide — Results Section 4.1–4.6

Manuscript: **A Reproducible Energy-Aware Benchmarking Framework for Real-Time Assignment in Autonomous UGV Fleets**.

The exact five figure placements below follow the uploaded revised `Result Reproducible(1).docx`. The files use `Fig01`–`Fig05` numbering; convert to the journal's final auto-numbered `Fig. 1`–`Fig. 5` captions when adding to the manuscript. Figures S1–S2 belong in Supplementary Material.

| Manuscript location | Figure | Insert after | Scientific purpose |
|---|---|---|---|
| Section 4.1 | **No figure** | — | Table R1 is sufficient; a checksum flowchart is better suited to Methods/Supplement if required. |
| Section 4.2 | **Fig. 1** `Fig01_Nominal_Energy_Wait_Tradeoff` | The paragraph beginning **“In S1–S3, substantial energy–waiting-time trade-offs...”** and before **“By comparison, Hungarian...”** | Four-panel comparison of relative modeled gross battery energy vs mean pickup-wait difference from Hungarian, S1–S4. |
| Section 4.3 | **Fig. 2** `Fig02_Energy_Ablation_Paired_Effects` | The paragraph ending **“...distinct from the pickup-response metric reported for completed requests in Section 4.2.”** | Energy-ablation effect intervals and secondary waiting-time effect intervals, paired n=18. |
| Section 4.4 | **Fig. 3** `Fig03_S4_S5_Fulfillment_Change` | The paragraph beginning **“The reduction in passenger fulfillment differed substantially...”**, before **“Battery-feasibility diagnostics...”** | Matched-demand descriptive S4-to-S5 fulfillment contrast for all eight algorithms. |
| Section 4.5 | **Fig. 4** `Fig04_S5_Fulfillment_Inference` | After **Table R5** and its notes, before the paragraph beginning **“Random and Round-Robin exhibited...”** | Effect sizes with unadjusted 95% paired CIs and Holm-significance decisions for S5 vs Hungarian. |
| Section 4.6 | **Fig. 5** `Fig05_Dispatch_Latency_and_Budget` | After **Table R7** and its notes, before the paragraph beginning **“The S5 findings reveal...”** | Primary dispatch p50/p95/p99 alongside S5 algorithm-level over-budget rates, clearly separated. |
| Supplement related to 4.3 | **Fig. S1** `FigS01_Exploratory_Physics_Sensitivity` | Supplementary Material immediately after the physical-model sensitivity table | 24 observations: six one-factor variants × four scenario/algorithm groups; exploratory, two seeds per group. |
| Supplement related to 4.6 | **Fig. S2** `FigS02_Dispatch_Phase_Attribution` | Supplementary Material alongside the phase timing audit | Percentage of *summed measured wall time* attributable to each phase, overall vs missed dispatches. |

## Publication-style captions

**Fig. 1.** Modeled energy–service trade-offs across four nominal UGV fleet–demand scenarios. Each point denotes the mean of 20 runs for one assignment algorithm. The horizontal axis reports the algorithm's mean pickup waiting time for picked-up passengers minus the Hungarian mean; the vertical axis reports its mean gross battery-energy withdrawal relative to Hungarian. Dashed lines identify Hungarian's reference values. Negative horizontal differences indicate shorter waiting, whereas a ratio greater than one indicates higher modeled gross battery-energy withdrawal. Fulfillment differs slightly among algorithms in S4, so waiting-time values must be interpreted alongside Table R2. Descriptive results, not tests of algorithm superiority.

**Fig. 2.** Paired effects of enabling energy weighting in S3 and S4. Points represent mean within-seed differences (energy-weighted minus energy-neutral); horizontal lines show unadjusted 95% paired Student-t confidence intervals across 18 matched seeds per comparison. Panel (a) reports gross modeled battery-energy withdrawal (Wh/run); filled points indicate comparisons significant after Holm correction across four exact sign-flip tests. Panel (b) reports the exploratory 480 s deadline-limited pickup-wait endpoint; its intervals are not multiplicity-adjusted. Negative energy effects favor energy weighting; positive waiting-time effects indicate longer waits.

**Fig. 3.** Passenger-request fulfillment under nominal S4 (100% initial battery SOC) and supplementary S5 (25% initial SOC). Every algorithm was evaluated over 20 matched demand realizations, corresponding to 2,000 request opportunities per condition. Lines connect descriptive aggregate completion percentages, and right-side values report S5 minus S4 in percentage points. The aggregate reductions do not constitute independent paired significance tests or isolate effects on dispatch execution time.

**Fig. 4.** Paired passenger-fulfillment effects relative to Hungarian under supplementary S5. Mean differences are computed per 100 generated requests across 20 matched seeds; horizontal lines are unadjusted paired 95% Student-t confidence intervals. Filled squares denote comparisons significant at the familywise 5% level using two-sided exact sign-flip tests with Holm correction for seven contrasts. The right panel magnifies the five comparisons close to zero; it repeats the same data, not an independent dataset. A non-significant difference does not establish equivalence.

**Fig. 5.** Empirical dispatch responsiveness under the accepted simulation implementation. Panel (a) shows scenario-level pooled dispatch p50/p95/p99 latencies for S1–S4 and the configured 300 ms budget. Panel (b) shows the proportion of S5 dispatch invocations exceeding 300 ms by algorithm. Campaigns are shown in separate panels because they differed in initial SOC and the documented Linux kernel revision. These descriptive host-timing measurements do not establish hard real-time compliance.

**Fig. S1.** Exploratory one-factor-at-a-time sensitivity of modeled gross battery-energy withdrawal to vehicle-physics assumptions. Each marker represents the percent change in the mean of two matched runs compared with the baseline within its own scenario–algorithm group. The points are not confidence intervals and do not establish physical calibration or robust algorithm superiority under other model parameters.

**Fig. S2.** Attribution of the summed recorded dispatch-processing wall time to candidate-cost-matrix construction, solver, cardinality diagnostics, activation and remaining operations. Values compare all 25,512 primary-campaign dispatches with the 77 dispatches exceeding 300 ms. Shares are based on summed phase durations, not median per-dispatch shares.

## Editorial notes

- Refer to each figure in the text **before** it appears, e.g., “The energy–waiting-time trade-off is illustrated in Fig. 1.”
- Do not duplicate full Table R2 or R5 values in figure labels. The figures are visual complements to the tables.
- Keep Fig. 1–5 at **two-column width** in two-column layouts because they contain multiple panels or long algorithm labels. They were authored at approximately 7.2 inches wide and use embedded fonts or editable vector text.
- Prefer `.pdf` for LaTeX / Overleaf, `.svg` or `.png` for Word (depending on your Word version), and `.png` at 600 dpi when a raster format is required.
- Original evidence inputs are SHA-256-checked before generation; this is reproducibility of figures, **not** validation of the physical UGV model.
