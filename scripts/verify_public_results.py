from pathlib import Path
from collections import Counter
from hashlib import sha256
import csv

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = "7b52bb550414469937da3098f91313b366137d43bd48b8465b840a97bffeb0a0"
SCENARIOS = {"S1": (12, 30), "S2": (18, 50),
             "S3": (24, 100), "S4": (12, 100)}

def check(ok, message):
    if not ok:
        raise SystemExit("FAIL: " + message)

def rows(name):
    with (ROOT / name).open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def key(row, fields):
    return tuple(row[f] for f in fields)

def index_of(records, fields, label):
    result = {key(r, fields): r for r in records}
    check(len(result) == len(records), "Duplicate keys in " + label)
    return result

def safe_path(value):
    path = Path(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts

print("===== GITHUB-11C: PUBLIC DATA VERIFICATION =====")

source = ROOT / "src/frozen/app.py"
check(source.is_file(), "Frozen source missing")
check(sha256(source.read_bytes()).hexdigest() == EXPECTED,
      "Frozen source identity")
print("PASS | Frozen FIX-19 simulator")

primary = rows("data/primary/run_level_640.csv")
pm = rows("config/primary/campaign_640_manifest.csv")
pi = rows("verification/primary/archive_index_640.csv")
wh = rows("data/primary/paired_workload_hashes_80.csv")

fields = ("scenario", "algorithm", "trial_index", "trial_seed")
check(len(primary) == len(pm) == len(pi) == 640,
      "Primary run counts")

runmap = index_of(primary, fields, "primary data")
manifestmap = index_of(pm, fields, "primary manifest")
pimap = index_of(pi, fields, "primary archive index")

check(set(runmap) == set(manifestmap) == set(pimap),
      "Primary manifest and archive identities")

whmap = index_of(
    wh, ("scenario", "trial_index", "trial_seed"),
    "primary workload hashes"
)
check(len(whmap) == 80, "Primary workload count")

counts = Counter()
algorithms = set()

for identity, row in runmap.items():
    scenario = row["scenario"]
    check(scenario in SCENARIOS, "Unknown primary scenario")

    vehicles, requests = SCENARIOS[scenario]
    check((int(row["vehicles"]), int(row["requests"]))
          == (vehicles, requests), "Primary workload size")

    check(int(row["completed"]) + int(row["uncompleted"])
          == requests, "Primary request accounting")

    archive = pimap[identity]
    check(safe_path(row["archive"]) and row["archive"]
          == archive["archive_relative_to_research_revision_v2"],
          "Primary archive identity")

    check(int(row["completed"])
          == int(archive["completed_requests"]),
          "Primary archive completion")

    check(int(row["dispatch_count"])
          == int(archive["dispatch_count"]),
          "Primary archive dispatch count")

    workload_key = key(
        row, ("scenario", "trial_index", "trial_seed")
    )
    check(workload_key in whmap, "Missing primary workload")
    check(
        whmap[workload_key]["canonical_workload_sha256"]
        == archive["canonical_workload_sha256"],
        "Primary workload hash"
    )

    counts[scenario] += 1
    algorithms.add(row["algorithm"])

check(len(algorithms) == 8, "Algorithm count")
check(all(counts[s] == 160 for s in SCENARIOS),
      "Primary scenario balance")

check(sum(int(r["requests"]) for r in primary) == 44800,
      "Primary generated requests")
check(sum(int(r["completed"]) for r in primary) == 44762,
      "Primary completed requests")
check(sum(int(r["dispatch_count"]) for r in primary) == 25512,
      "Primary dispatches")
check(sum(int(r["dispatch_over_300ms"]) for r in primary) == 77,
      "Primary timing overruns")

print("PASS | Primary: 640 runs")
print("PASS | 44,762/44,800 completed")
print("PASS | 25,512 dispatches; 77 overruns")

s5 = rows("data/s5/run_level_160.csv")
sm = rows("config/s5/step117b_s5_160_manifest.csv")
si = rows("verification/s5/archive_index_160.csv")

sfields = ("algorithm", "trial_index", "trial_seed")
check(len(s5) == len(sm) == len(si) == 160, "S5 run counts")

s5map = index_of(s5, sfields, "S5 data")
s5manifest = index_of(sm, sfields, "S5 manifest")
s5index = index_of(si, sfields, "S5 archive index")

check(set(s5map) == set(s5manifest) == set(s5index),
      "S5 manifest and archive identities")

for identity, row in s5map.items():
    archive = s5index[identity]

    check(float(row["initial_soc_percent"]) == 25.0,
          "S5 initial SOC")

    check(int(row["completed"]) + int(row["uncompleted"])
          == 100, "S5 request accounting")

    check(safe_path(row["archive"]) and row["archive"]
          == archive["archive_relative_to_research_revision_v2"],
          "S5 archive identity")

    check(int(row["completed"]) == int(archive["completed"]),
          "S5 archive completion")

    check(int(row["dispatches"]) == int(archive["dispatches"]),
          "S5 archive dispatch count")

    workload_key = (
        "S4", row["trial_index"], row["trial_seed"]
    )
    check(workload_key in whmap, "Missing matched S4 workload")

    workload_hash = row["canonical_workload_sha256"]
    check(
        workload_hash
        == whmap[workload_key]["canonical_workload_sha256"],
        "S5-to-S4 workload mismatch"
    )
    check(
        workload_hash == archive["canonical_workload_sha256"],
        "S5 archive workload mismatch"
    )

check({r["algorithm"] for r in s5} == algorithms,
      "Algorithm sets differ")

check(sum(int(r["completed"]) for r in s5) == 14982,
      "S5 completions")
check(sum(int(r["dispatches"]) for r in s5) == 10750,
      "S5 dispatches")
check(sum(int(r["over_300ms"]) for r in s5) == 389,
      "S5 timing overruns")

print("PASS | S5: 160 runs")
print("PASS | 14,982/16,000 completed")
print("PASS | 10,750 dispatches; 389 overruns")
print("PASS | 160 S5-to-S4 workload matches")

print("===== GITHUB-11C PASSED =====")
print("Verified: released summary data and recorded identities.")
print("Not verified: raw events or full simulation reproduction.")
