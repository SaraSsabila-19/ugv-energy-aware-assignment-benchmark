#!/usr/bin/env python3
"""FIX-19 / STEP 109A: Independent read-only 640-run scientific audit.

Reads archived simulation outputs, creates reports in a NEW analysis directory.
Never imports or executes app.py, starts simulations, or writes into run archives.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import csv
import hashlib
import json
import math
import random
import statistics
import zipfile

ROOT = Path("research_revision_v2").resolve()
FREEZE = ROOT / "step106b_frozen_candidate_20261029T174020Z"
MANIFEST = FREEZE / "campaign_640_manifest.csv"
REPORT = ROOT / "step108_full640_unified_campaign/campaign_640_progress.json"
SOURCE = ROOT / "fix19_candidate/app.py"
FIX20 = ROOT / "fix20_candidate/app.py"
FROZEN_SOURCE = FREEZE / "simulator/app.py"
FROZEN_ZIP = FREEZE / "frozen_bundle.zip"
SOURCE_SHA = "7b52bb550414469937da3098f91313b366137d43bd48b8465b840a97bffeb0a0"
FIX20_SHA = "1ae0e683ae4d6f7839841c5bf2123770e8d86482819679a7f533d465d920153d"
MANIFEST_SHA = "33361ce7a27c547cfa0dbb1a1fd37506b75d263d3ccf2334805428d545096189"
ZIP_SHA = "f44c29293608268eae77ab5c4fe1a5a890e0671af78cd0ac100ccae8af04683b"
ALGOS = ("greedy", "hungarian", "mcmf", "auction", "random", "roundrobin", "greedy_minmax", "annealing")
SCENARIOS = ("S1", "S2", "S3", "S4")
SIZES = {"S1": (12, 30), "S2": (18, 50), "S3": (24, 100), "S4": (12, 100)}
ARCHIVE_FILES = (
    "run_settings.json", "headless_status.json", "canonical_requests.csv",
    "request_results.csv", "vehicle_results.csv", "run_summary.csv",
    "vehicle_soc_snapshots.csv", "charging_events.csv",
    "assignment_events.csv", "dispatch_invocation_events.csv", "timing.csv",
    "backend_source.py", "requirements-freeze.txt",
)


def check(condition, message):
    if not condition:
        raise RuntimeError("STOP: " + message)


def sha(path):
    check(path.is_file() and not path.is_symlink(), f"Missing/linked file {path}")
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def csvrows(path):
    check(path.is_file() and not path.is_symlink(), f"Missing/linked CSV {path}")
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        check(reader.fieldnames is not None, f"Missing header: {path}")
        return list(reader)


def number(row, name):
    value = float(row[name])
    check(math.isfinite(value), f"Nonfinite {name}")
    return value


def integer(row, name):
    value = number(row, name)
    check(value.is_integer(), f"Nonintegral {name}={value}")
    return int(value)


def key(row):
    return (row["scenario"], row["algorithm"], int(row["trial_index"]))


def safe_json(path):
    check(path.is_file() and not path.is_symlink(), f"Missing/linked JSON {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_csv(path, records, fields):
    with path.open("x", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def pctl(sorted_values, p):
    if not sorted_values:
        return None
    x = (len(sorted_values) - 1) * p
    i = int(x)
    f = x - i
    if i == len(sorted_values) - 1:
        return sorted_values[-1]
    return sorted_values[i] * (1 - f) + sorted_values[i + 1] * f


def paired_bootstrap(diffs, seed, resamples=5000):
    check(len(diffs) == 20, "Paired bootstrap needs all 20 seeds")
    rng = random.Random(seed)
    n = len(diffs)
    estimates = []
    for _ in range(resamples):
        estimates.append(math.fsum(diffs[rng.randrange(n)] for _ in range(n)) / n)
    estimates.sort()
    return pctl(estimates, 0.025), pctl(estimates, 0.975)


print("===== FIX-19 / STEP 109A: INDEPENDENT FULL-CAMPAIGN AUDIT =====", flush=True)
protected = {
    SOURCE: SOURCE_SHA,
    FIX20: FIX20_SHA,
    FROZEN_SOURCE: SOURCE_SHA,
    MANIFEST: MANIFEST_SHA,
    FROZEN_ZIP: ZIP_SHA,
}
for f, expected in protected.items():
    check(sha(f) == expected, f"Protected hash changed: {f}")
checksum_inventory = {}
for line in (FREEZE / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
    digest, name = line.split("  ", 1)
    checksum_inventory[name] = digest
for filename, digest in checksum_inventory.items():
    check(sha(FREEZE / filename) == digest, f"Freeze checksum changed: {filename}")
print("PASS: Protected code, manifest, ZIP and frozen checksum inventory", flush=True)

manifest = csvrows(MANIFEST)
check(len(manifest) == 640, "Manifest is not 640 rows")
manifest_by_key = {key(r): r for r in manifest}
expected_keys = {(s, a, t) for s in SCENARIOS for a in ALGOS for t in range(20)}
check(len(manifest_by_key) == 640 and set(manifest_by_key) == expected_keys,
      "Manifest keys incomplete or duplicated")

progress = safe_json(REPORT)
check(progress.get("planned") == 640 and progress.get("campaign") == "FIX19_640", "Campaign report identity")
check(progress.get("source_sha256") == SOURCE_SHA and progress.get("manifest_sha256") == MANIFEST_SHA,
      "Campaign report source/manifest identity")
records = progress["records"]
check(len(records) == 640, f"Expected 640 records; found {len(records)}")
by_key = {}
origin_counts = Counter()
for record in records:
    k = key(record)
    check(k not in by_key, f"Duplicate report key {k}")
    check(record["status"] == "VALIDATED", f"Unvalidated record {k}")
    check(integer(record, "trial_seed") == 1001 + 1000 * k[2], f"Seed mismatch {k}")
    by_key[k] = record
    origin_counts[record.get("origin", "unknown")] += 1
check(set(by_key) == expected_keys, "Campaign missing manifest records")
check(origin_counts["prevalidated_step107_or_108a"] == 32,
      "Expected 32 previously validated results")
check(origin_counts["step108_unified_execution"] == 608,
      "Expected 608 newly executed results")
print("PASS: 640 unique records = 32 prior + 608 new", flush=True)

run_data = []
run_by_key = {}
canonical_by_pair = {}
trace_by_key = {}
energy_ties_by_pair = defaultdict(list)
all_dispatch = defaultdict(list)

for idx, k in enumerate(sorted(expected_keys, key=lambda x: (x[2], SCENARIOS.index(x[0]), ALGOS.index(x[1]))), 1):
    scenario, algorithm, trial = k
    seed = 1001 + 1000 * trial
    record = by_key[k]
    m = manifest_by_key[k]
    vehicle_count, request_count = SIZES[scenario]
    check(int(m["trial_seed"]) == seed, f"Manifest seed mismatch {k}")
    check((int(m["vehicles"]), int(m["requests"])) == (vehicle_count, request_count),
          f"Manifest fleet/workload mismatch {k}")
    archive = Path(record["archive"]).resolve()
    check(archive.is_dir() and archive.is_relative_to(ROOT), f"Unexpected archive {k}: {archive}")
    checksums = record.get("archive_checksums")
    check(isinstance(checksums, dict) and set(checksums) == set(ARCHIVE_FILES),
          f"Archive checksum inventory incomplete: {k}")
    for filename in ARCHIVE_FILES:
        check(sha(archive / filename) == checksums[filename],
              f"Archive changed after validation: {k} / {filename}")
    settings = safe_json(archive / "run_settings.json")
    status = safe_json(archive / "headless_status.json")
    check(status["status"] == "completed" and status.get("error") is None, f"Incomplete archive {k}")
    check(settings["backend_source"]["sha256"] == SOURCE_SHA and sha(archive / "backend_source.py") == SOURCE_SHA,
          f"Wrong source in archive {k}")
    check(settings["scenario"] == scenario and settings["algorithm"] == algorithm,
          f"Wrong archived scenario or algorithm {k}")
    check(int(settings["configured_seeds"]["TRIAL_SEED"]) == seed, f"Archived seed {k}")
    check(settings["depot_layout"] == "three" and settings["three_depot_selection"]["parking_ids"] == ["P02", "P08", "P16"],
          f"Depot setting mismatch {k}")
    p = settings["parameters"]
    for name, value in (("BATTERY_CAPACITY_WH", 9000), ("CHARGER_DC_POWER_W", 1500),
                        ("ALPHA_ENERGY", 1), ("BETA_TIME", 1), ("SOC_COST_WEIGHT", 0),
                        ("DISPATCH_WINDOW_S", 30), ("DISPATCH_URGENT_SLACK_S", 60),
                        ("COST_ENERGY_REFERENCE_WH", 100), ("COST_TIME_REFERENCE_S", 600)):
        check(abs(number(p, name) - value) < 1e-8, f"Benchmark setting mismatch {k}: {name}")
    check(p["UGV_HVAC_MODE"] == "off" and p["ASSIGNMENT_COST_MODE"] == "normalized", f"Cost/HVAC mismatch {k}")

    canonical_digest = sha(archive / "canonical_requests.csv")
    check(canonical_digest == record["canonical_sha256"], f"Canonical checksum mismatch {k}")
    pair = (scenario, trial)
    if pair in canonical_by_pair:
        check(canonical_by_pair[pair] == canonical_digest, f"Paired workload mismatch {k}")
    canonical_by_pair[pair] = canonical_digest

    requests = csvrows(archive / "request_results.csv")
    vehicles = csvrows(archive / "vehicle_results.csv")
    summary_rows = csvrows(archive / "run_summary.csv")
    dispatch_rows = csvrows(archive / "dispatch_invocation_events.csv")
    assignment_rows = csvrows(archive / "assignment_events.csv")
    check(len(requests) == request_count and len(vehicles) == vehicle_count and len(summary_rows) == 1,
          f"Missing request/vehicle/summary rows {k}")
    sm = summary_rows[0]
    completed = sum(integer(q, "request_completed") for q in requests)
    failures = sum(integer(q, "request_completed") == 0 for q in requests)
    check(completed + failures == request_count, f"Request accounting mismatch {k}")
    check(integer(sm, "completed_requests") == completed and integer(sm, "uncompleted_requests") == failures,
          f"Summary completion mismatch {k}")
    battery = math.fsum(number(q, "energy_consumed_Wh") for q in vehicles)
    grid = number(sm, "total_grid_import_Wh")
    check(abs(battery - number(sm, "total_energy_consumed_Wh")) < .05,
          f"Battery accounting mismatch {k}")
    check(abs(battery - number(record, "battery_Wh")) < .05,
          f"Campaign reported energy mismatch {k}")
    check(abs(grid - number(record, "grid_Wh")) < .05,
          f"Campaign reported grid energy mismatch {k}")
    capped_wait = number(sm, "mean_deadline_limited_pickup_wait_s")
    check(integer(sm, "pickup_wait_request_count") == request_count,
          f"Capped wait sample count mismatch {k}")
    check(integer(sm, "pickup_wait_incomplete_count") == 0,
          f"Unresolved pickup observations {k}")
    pickup_response = (number(sm, "mean_pickup_response_picked_up_s")
                       if sm.get("mean_pickup_response_picked_up_s") not in (None, "") else None)
    distance = number(sm, "total_distance_km")
    budget_misses = integer(sm, "dispatch_budget_miss_count")
    latencies = [number(q, "complete_dispatch_ms") for q in dispatch_rows]
    check(len(latencies) == integer(sm, "dispatch_count"), f"Dispatch count mismatch {k}")
    check(sum(t > 300 for t in latencies) == budget_misses, f"Dispatch deadline mismatches {k}")
    check(budget_misses == integer(record, "dispatch_over_300ms"), f"Campaign deadline mismatches {k}")
    check(abs(max(latencies, default=0.0) - (record.get("dispatch_max_ms") or 0.0)) < .01,
          f"Dispatch maximum mismatch {k}")
    all_dispatch[(scenario, algorithm)].extend(latencies)
    assignment_trace = sorted((q["request_id"], q["vehicle_id"], q["event_time_s"]) for q in assignment_rows)
    trace_by_key[k] = hashlib.sha256(json.dumps(assignment_trace, separators=(",", ":")).encode()).hexdigest()
    check(len({q["request_id"] for q in assignment_rows}) == len(assignment_rows),
          f"Duplicate committed requests in assignments {k}")
    check(completed <= len(assignment_rows) <= request_count,
          f"Assignment count inconsistent with completed requests {k}")

    output = dict(scenario=scenario, algorithm=algorithm, trial_index=trial,
                  trial_seed=seed, vehicles=vehicle_count, requests=request_count,
                  completed=completed, uncompleted=failures,
                  fulfillment_pct=100.0 * completed / request_count,
                  battery_Wh=battery, grid_Wh=grid, total_distance_km=distance,
                  mean_deadline_limited_pickup_wait_s=capped_wait,
                  mean_pickup_response_picked_up_s=pickup_response,
                  dispatch_count=len(latencies), dispatch_over_300ms=budget_misses,
                  dispatch_max_ms=max(latencies, default=0.0),
                  mean_solver_ms=(number(sm, "assignment_runtime_ms")
                                  if sm.get("assignment_runtime_ms") not in (None, "") else None),
                  matching_gap_rounds=integer(sm, "matching_cardinality_gap_rounds"),
                  archive=str(archive))
    run_data.append(output)
    run_by_key[k] = output
    if idx % 80 == 0:
        print(f"Audited archives: {idx}/640", flush=True)

check(len(canonical_by_pair) == 80, "Expected 80 unique scenario/seed workloads")
print("PASS: All 640 archive checksums, identities, and summary reconstructions", flush=True)
print("PASS: All 80 scenario/seed workloads paired across eight algorithms", flush=True)

scenario_summary = []
for scenario in SCENARIOS:
    for algorithm in ALGOS:
        block = [run_by_key[(scenario, algorithm, t)] for t in range(20)]
        lat = sorted(all_dispatch[(scenario, algorithm)])
        scenario_summary.append({
            "scenario": scenario,
            "algorithm": algorithm,
            "seeds": 20,
            "mean_completed": statistics.mean(q["completed"] for q in block),
            "sd_completed": statistics.stdev(q["completed"] for q in block),
            "mean_fulfillment_pct": statistics.mean(q["fulfillment_pct"] for q in block),
            "mean_battery_Wh": statistics.mean(q["battery_Wh"] for q in block),
            "sd_battery_Wh": statistics.stdev(q["battery_Wh"] for q in block),
            "mean_grid_Wh": statistics.mean(q["grid_Wh"] for q in block),
            "mean_distance_km": statistics.mean(q["total_distance_km"] for q in block),
            "mean_deadline_limited_pickup_wait_s": statistics.mean(q["mean_deadline_limited_pickup_wait_s"] for q in block),
            "mean_pickup_response_picked_up_s": (statistics.mean(q["mean_pickup_response_picked_up_s"] for q in block)
                  if all(q["mean_pickup_response_picked_up_s"] is not None for q in block) else None),
            "dispatch_calls": len(lat),
            "dispatch_over_300ms": sum(q["dispatch_over_300ms"] for q in block),
            "dispatch_budget_miss_pct": 100.0 * sum(q["dispatch_over_300ms"] for q in block) / len(lat) if lat else None,
            "pooled_dispatch_p95_ms": pctl(lat, .95),
            "pooled_dispatch_p99_ms": pctl(lat, .99),
            "pooled_dispatch_max_ms": max(lat, default=None),
            "mean_solver_ms": (statistics.mean(q["mean_solver_ms"] for q in block)
                 if all(q["mean_solver_ms"] is not None for q in block) else None),
            "total_matching_gap_rounds": sum(q["matching_gap_rounds"] for q in block),
            "same_assignment_trace_as_hungarian_seeds": sum(
                trace_by_key[(scenario, algorithm, t)] == trace_by_key[(scenario, "hungarian", t)]
                for t in range(20)),
            "same_battery_Wh_as_hungarian_seeds": sum(
                abs(run_by_key[(scenario, algorithm, t)]["battery_Wh"] -
                    run_by_key[(scenario, "hungarian", t)]["battery_Wh"]) < 0.001
                for t in range(20)),
        })

comparisons = []
metrics = (
    ("battery_Wh", "lower is better"),
    ("completed", "higher is better"),
    ("mean_deadline_limited_pickup_wait_s", "lower is better; includes capped waiting for missed pickups"),
)
for scenario in SCENARIOS:
    for algorithm in ALGOS:
        if algorithm == "hungarian":
            continue
        for metric_index, (metric, meaning) in enumerate(metrics):
            diffs = [run_by_key[(scenario, algorithm, t)][metric] -
                     run_by_key[(scenario, "hungarian", t)][metric] for t in range(20)]
            lo, hi = paired_bootstrap(diffs, 109000 + SCENARIOS.index(scenario) * 1000 +
                                      ALGOS.index(algorithm) * 10 + metric_index)
            comparisons.append({"scenario": scenario, "algorithm": algorithm,
                                "reference": "hungarian", "metric": metric,
                                "direction": meaning, "n_paired_seeds": 20,
                                "mean_paired_difference": statistics.mean(diffs),
                                "median_paired_difference": statistics.median(diffs),
                                "bootstrap_95pct_lo": lo, "bootstrap_95pct_hi": hi,
                                "wins": sum((d < 0 if "lower is better" in meaning else d > 0) for d in diffs),
                                "losses": sum((d > 0 if "lower is better" in meaning else d < 0) for d in diffs),
                                "ties": sum(d == 0 for d in diffs)})

# Do not modify the freeze or campaign: write only to a distinct new folder.
output_dir = ROOT / ("step109a_independent_analysis_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
output_dir.mkdir(exist_ok=False)
save_csv(output_dir / "run_level_640.csv", run_data, list(run_data[0]))
save_csv(output_dir / "scenario_algorithm_summary.csv", scenario_summary, list(scenario_summary[0]))
save_csv(output_dir / "paired_vs_hungarian.csv", comparisons, list(comparisons[0]))
json_report = {
    "status": "INDEPENDENT_ARCHIVE_AUDIT_PASSED",
    "frozen_source_sha256": SOURCE_SHA,
    "manifest_sha256": MANIFEST_SHA,
    "runs_validated": len(run_data),
    "unique_paired_scenario_seed_workloads": len(canonical_by_pair),
    "source_run_origins": dict(origin_counts),
    "scenario_algorithm_summary": scenario_summary,
    "paired_comparison_note": "Paired bootstrap percentile intervals are exploratory, unadjusted for multiple comparisons; not proof of statistical significance or causality.",
    "physical_model_note": "SOC/charging checks demonstrate internal consistency, not measured hardware accuracy.",
    "real_time_note": "Dispatch >300ms measured, not guaranteed deadline; hardware/software setting matters.",
    "energy_aware_note": "A 100%-SOC alpha=1 main campaign alone does not establish benefits attributable to energy weighting; use an explicit alpha=0 ablation.",
}
(output_dir / "step109a_audit_report.json").write_text(json.dumps(json_report, indent=2, allow_nan=False), encoding="utf-8")
share_file = output_dir / "step109a_shareable_results.zip"
with zipfile.ZipFile(share_file, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
    for name in ("run_level_640.csv", "scenario_algorithm_summary.csv",
                 "paired_vs_hungarian.csv", "step109a_audit_report.json"):
        zipped.write(output_dir / name, arcname=name)
with zipfile.ZipFile(share_file) as zipped:
    check(zipped.testzip() is None, "Shareable ZIP validation failed")

for path, expected in protected.items():
    check(sha(path) == expected, f"Protected source changed during analysis: {path}")
for filename, digest in checksum_inventory.items():
    check(sha(FREEZE / filename) == digest, f"Frozen package changed during analysis: {filename}")

print("\n===== 20-SEED DESCRIPTIVE SUMMARY =====")
print(f"{'Scenario':8} {'Algorithm':16} {'Fulfil %':>9} {'Battery Wh':>12} {'Capped wait s':>14} {'>300ms':>8} {'Dispatch p99':>13}")
for r in scenario_summary:
    print(f"{r['scenario']:8} {r['algorithm']:16} {r['mean_fulfillment_pct']:9.2f} "
          f"{r['mean_battery_Wh']:12.2f} {r['mean_deadline_limited_pickup_wait_s']:14.2f} "
          f"{r['dispatch_over_300ms']:8d} {r['pooled_dispatch_p99_ms']:13.3f}")
print("\n===== SCIENTIFIC CAUTIONS =====")
print("- All algorithm comparisons use paired scenario/seed workloads.")
print("- Passenger fulfillment must be interpreted alongside total battery energy.")
print("- Capped pickup wait treats missed pickups as 480 s, not as missing observations.")
print("- Bootstrap confidence intervals are exploratory and NOT multiple-comparison adjusted.")
print("- Energy ledger conservation is not physical energy-model calibration.")
print("- Dispatch timing is not a hard real-time guarantee.")
print("- Main alpha=1 campaign alone cannot prove causal benefits of energy weighting.")
print("\nOutput directory:", output_dir)
print("Run-level metrics:", output_dir / "run_level_640.csv")
print("Scenario summary:", output_dir / "scenario_algorithm_summary.csv")
print("Paired comparisons:", output_dir / "paired_vs_hungarian.csv")
print("Audit record:", output_dir / "step109a_audit_report.json")
print("Upload this ONE file for full analysis:", share_file)
print("Original sources, archives, manifest: UNCHANGED")
print("===== STEP 109A COMPLETE =====")
