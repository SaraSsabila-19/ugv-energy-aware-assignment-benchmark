#!/usr/bin/env python3
"""FIX-19 / STEP 111B — isolated parameter-sensitivity PILOT.

This is an uncertainty/sensitivity test, NOT experimental calibration of a real
vehicle. It intentionally creates labeled, modified COPIES of FIX-19 source;
the frozen FIX-19 source, FIX-20 and 640-run archives are never edited.

7 variants x 2 scenarios x 2 algorithms x 2 seeds = 56 isolated runs.
All alternative parameter values are ILLUSTRATIVE stress tests, not verified
vehicle specifications, uncertainty bounds, or literature-backed estimates.
"""
from __future__ import annotations
import ast
import csv
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

ROOT = Path('research_revision_v2').resolve()
FREEZE = ROOT / 'step106b_frozen_candidate_20261029T174020Z'
SOURCE = FREEZE / 'simulator'
WORK = ROOT / 'step111b_physical_parameter_sensitivity_pilot'
REPORT = WORK / 'step111b_progress.json'
PROTOCOL_FILE = WORK / 'step111b_protocol.json'
BASE_SOURCE_SHA = '7b52bb550414469937da3098f91313b366137d43bd48b8465b840a97bffeb0a0'
PROTECTED = {
    ROOT / 'fix19_candidate/app.py': BASE_SOURCE_SHA,
    ROOT / 'fix20_candidate/app.py': '1ae0e683ae4d6f7839841c5bf2123770e8d86482819679a7f533d465d920153d',
    SOURCE / 'app.py': BASE_SOURCE_SHA,
    SOURCE / 'utp_graph.graphml': 'ebd138a83763f11172e079f40457b371be7eebcd2e3aa018aeda13d9d70114f4',
    SOURCE / 'hotspot_locations.csv': '6a55a275f2ac18ab383d2efcd67a31250602503a62fa3c5bf2f3f504ae0b979d',
    FREEZE / 'campaign_640_manifest.csv': '33361ce7a27c547cfa0dbb1a1fd37506b75d263d3ccf2334805428d545096189',
    FREEZE / 'frozen_bundle.zip': 'f44c29293608268eae77ab5c4fe1a5a890e0671af78cd0ac100ccae8af04683b',
}
BASELINE = {
    'UGV_C_RR': 0.012,
    'UGV_ETA': 0.90,
    'UGV_BASE_AUX_POWER_W': 50.0,
    'UGV_EMPTY_MASS_KG': 825.0,
}
# One factor at a time; comparison is only to baseline at the same scenario,
# algorithm and seed. Ranges are illustrative, not derived from measurements.
VARIANTS = (
    ('baseline', None, None),
    ('rolling_low', 'UGV_C_RR', 0.008),
    ('rolling_high', 'UGV_C_RR', 0.020),
    ('efficiency_low', 'UGV_ETA', 0.80),
    ('efficiency_high', 'UGV_ETA', 0.95),
    ('auxiliary_high', 'UGV_BASE_AUX_POWER_W', 250.0),
    ('mass_high', 'UGV_EMPTY_MASS_KG', 1025.0),
)
SCENARIOS = ('S3', 'S4')
ALGORITHMS = ('hungarian', 'greedy')
TRIALS = (0, 1)
SIZES = {'S3': (24, 100), 'S4': (12, 100)}
ARCHIVE_FILES = (
    'headless_status.json', 'run_settings.json', 'backend_source.py',
    'canonical_requests.csv', 'request_results.csv', 'vehicle_results.csv',
    'vehicle_soc_snapshots.csv', 'charging_events.csv',
    'dispatch_invocation_events.csv', 'run_summary.csv', 'timing.csv',
    'assignment_events.csv',
)

def sha(path):
    p = Path(path)
    assert p.is_file() and not p.is_symlink(), f'Missing file/symlink: {p}'
    h = hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def csvrows(path):
    assert Path(path).is_file(), f'Missing CSV: {path}'
    with Path(path).open(newline='', encoding='utf-8-sig') as f:
        rdr = csv.DictReader(f)
        assert rdr.fieldnames, f'CSV header missing: {path}'
        return list(rdr)


def num(row, key):
    v = float(row[key])
    assert math.isfinite(v), f'Nonfinite {key}'
    return v


def utc():
    return datetime.now(timezone.utc).isoformat()


def key(rec):
    return (rec['variant'], rec['scenario'], rec['algorithm'], int(rec['trial_index']))


def atomic_json(path, data):
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def source_with_variant(original: str, name: str, changed: str | None, newvalue: float | None):
    """Change EXACTLY ONE top-level literal assignment, using the parsed AST."""
    assert sha_bytes(original.encode('utf-8')) == BASE_SOURCE_SHA
    if changed is None:
        assert name == 'baseline' and newvalue is None
        return original
    assert changed in BASELINE and isinstance(newvalue, float)
    tree = ast.parse(original, filename='frozen_FIX19_app.py')
    locations = []
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            if isinstance(target, ast.Name) and target.id == changed:
                assert statement.lineno == statement.end_lineno
                assert isinstance(statement.value, ast.Constant)
                assert math.isclose(float(statement.value.value), BASELINE[changed], abs_tol=1e-12)
                locations.append(statement.lineno)
    assert len(locations) == 1, f'Must find exactly one assignable {changed}; got {locations}'
    lines = original.splitlines(keepends=True)
    idx = locations[0] - 1
    assert lines[idx].startswith(changed + ' =')
    ending = '\n' if lines[idx].endswith('\n') else ''
    lines[idx] = f'{changed} = {newvalue!r}  # STEP111B illustrative {name}{ending}'
    modified = ''.join(lines)
    compile(modified, 'step111b_variant_app.py', 'exec')
    assert modified != original
    newtree = ast.parse(modified)
    found = []
    for statement in newtree.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            if isinstance(target, ast.Name) and target.id == changed:
                found.append(float(ast.literal_eval(statement.value)))
    assert found == [newvalue]
    return modified


def final_status(stdout):
    decoder = json.JSONDecoder()
    for i in range(len(stdout) - 1, -1, -1):
        if stdout[i] != '{':
            continue
        try:
            obj, _ = decoder.raw_decode(stdout[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and 'results_dir' in obj:
            return obj
    raise RuntimeError('Missing headless simulation status in stdout')


print('===== FIX-19 / STEP 111B: ILLUSTRATIVE PHYSICAL SENSITIVITY PILOT =====', flush=True)
frozen_inventory = {}
for line in (FREEZE / 'SHA256SUMS.txt').read_text().splitlines():
    digest, name = line.split('  ', 1)
    frozen_inventory[name] = digest

def check_protected():
    for path, digest in PROTECTED.items():
        assert sha(path) == digest, f'PROTECTED HASH MISMATCH: {path}'
    for name, digest in frozen_inventory.items():
        assert sha(FREEZE / name) == digest, f'FROZEN BUNDLE MISMATCH: {name}'

check_protected()
record = json.loads((FREEZE / 'freeze_record.json').read_text())
assert record['freeze_status'] == 'FROZEN_CANDIDATE' and record['experiment_count'] == 640
for name, digest in record['source_tree'].items():
    assert sha(SOURCE / name) == digest
env_record = json.loads((FREEZE / 'environment.json').read_text())
assert sys.version == env_record['python']
assert platform.platform() == env_record['platform']
for package, version in env_record['packages'].items():
    assert metadata.version(package) == version
frozen_text = (SOURCE / 'app.py').read_text(encoding='utf-8')
assert sha_bytes(frozen_text.encode('utf-8')) == BASE_SOURCE_SHA
variant_codes = {}
for name, changed, value in VARIANTS:
    code = source_with_variant(frozen_text, name, changed, value)
    variant_codes[name] = dict(source=code, sha256=sha_bytes(code.encode('utf-8')),
                               changed_parameter=changed, new_value=value)
assert len({r['sha256'] for r in variant_codes.values()}) == len(VARIANTS)
print('PASS: Protected source hashes and frozen Python environment', flush=True)
print('IMPORTANT: Sensitivity variants are copies; these are NOT calibrated parameters.', flush=True)

specs = [dict(variant=v, scenario=s, algorithm=a, trial_index=t, trial_seed=1001 + 1000 * t)
         for v, _, _ in VARIANTS for s in SCENARIOS for a in ALGORITHMS for t in TRIALS]
assert len(specs) == 56 and len({key(x) for x in specs}) == 56
protocol = dict(
    step='111B', stage='EXPLORATORY_ONE_FACTOR_AT_A_TIME_SENSITIVITY',
    research_question='Sensitivity of simulated battery energy, passenger service and dispatch outcomes to provisional vehicle model coefficients.',
    frozen_source_sha256=BASE_SOURCE_SHA,
    original_manifest_sha256=PROTECTED[FREEZE / 'campaign_640_manifest.csv'],
    scenarios=list(SCENARIOS), algorithms=list(ALGORITHMS), trial_indices=list(TRIALS),
    baseline_coefficients=BASELINE,
    variants=[dict(name=n, changed_parameter=c, value=x, sha256=variant_codes[n]['sha256'])
              for n, c, x in VARIANTS],
    shared_operational_settings=dict(alpha_energy=1, beta_time=1, soc_cost_weight=0,
                                     initial_soc=1.0, battery_capacity_Wh=9000,
                                     reserve_fraction=.2, charger_dc_power_W=1500,
                                     HVAC='off', depot_ids=['P02', 'P08', 'P16'],
                                     max_sim_s=14400, dispatch_window_s=30,
                                     dispatch_urgent_slack_s=60),
    scientific_limits=[
        'Illustrative perturbations are not measured parameter error bounds.',
        'No physical vehicle-energy accuracy or calibration is established.',
        'One factor changes per variant; interactions are NOT estimated.',
        'Only two seeds per scenario/algorithm are used; results are exploratory.',
        'Changed variant source code is NOT the frozen 640-run benchmark.',
        'Dispatch wall-clock timings do NOT imply a hard real-time guarantee.',
    ],
    cases=specs,
)
WORK.mkdir(exist_ok=True)
if PROTOCOL_FILE.exists():
    assert json.loads(PROTOCOL_FILE.read_text()) == protocol, 'STOP: Protocol changed since last run'
else:
    assert not any(WORK.iterdir()), 'STOP: Untracked files in Step 111B directory'
    atomic_json(PROTOCOL_FILE, protocol)
if REPORT.exists():
    progress = json.loads(REPORT.read_text())
    assert progress['protocol_sha256'] == sha(PROTOCOL_FILE) and progress['planned'] == 56
else:
    assert {x.name for x in WORK.iterdir()} == {PROTOCOL_FILE.name}
    progress = dict(step='111B', protocol_sha256=sha(PROTOCOL_FILE),
                    planned=56, created_utc=utc(), records=[])
    atomic_json(REPORT, progress)

existing = {}
workloads = {}
for row in progress['records']:
    k = key(row)
    assert k in {key(x) for x in specs} and k not in existing
    assert row['status'] == 'VALIDATED', f'STOP: Nonvalidated prior result {k}; inspect without overwriting'
    archive = Path(row['archive']).resolve()
    assert archive.is_relative_to(WORK) and archive.is_dir()
    for filename, digest in row['archive_checksums'].items():
        assert sha(archive / filename) == digest, f'Prior archive hash drift: {k}/{filename}'
    wk = (row['scenario'], int(row['trial_index']))
    assert wk not in workloads or workloads[wk] == row['canonical_sha256']
    workloads[wk] = row['canonical_sha256']
    existing[k] = row
print(f'Existing validated: {len(existing)}/56; remaining: {56 - len(existing)}', flush=True)


def verify(archive, spec):
    v, s, a, t = key(spec)
    seed = int(spec['trial_seed'])
    fleet_size, request_count = SIZES[s]
    variant = variant_codes[v]
    for filename in ARCHIVE_FILES:
        assert (archive / filename).is_file(), f'Missing archive file {filename}'
    settings = json.loads((archive / 'run_settings.json').read_text())
    p = settings['parameters']
    status = json.loads((archive / 'headless_status.json').read_text())
    assert status['status'] == 'completed' and status['error'] is None
    assert (status['scenario'], status['algorithm'], int(status['trial_index']), int(status['trial_seed'])) == (s, a, t, seed)
    assert (settings['scenario'], settings['algorithm'], int(settings['trial_index']),
            int(settings['configured_seeds']['TRIAL_SEED'])) == (s, a, t, seed)
    assert settings['run_id'] == archive.name
    assert settings['backend_source']['sha256'] == variant['sha256']
    assert sha(archive / 'backend_source.py') == variant['sha256']
    assert settings['depot_layout'] == 'three'
    assert settings['three_depot_selection']['parking_ids'] == ['P02', 'P08', 'P16']
    assert settings['scenario_config']['initial_soc_range'] == [1.0, 1.0]
    assert len(settings['initial_fleet']) == fleet_size
    assert all(abs(num(x, 'soc_Wh') - 9000) < 0.01 for x in settings['initial_fleet'])
    assert p['UGV_HVAC_MODE'] == 'off' and p['ASSIGNMENT_COST_MODE'] == 'normalized'
    expected = {
        'UGV_C_RR': BASELINE['UGV_C_RR'],
        'UGV_ETA': BASELINE['UGV_ETA'],
        'UGV_EMPTY_MASS_KG': BASELINE['UGV_EMPTY_MASS_KG'],
        'UGV_DRIVING_AUX_POWER_W': BASELINE['UGV_BASE_AUX_POWER_W'],
        'UGV_IDLE_POWER_W': BASELINE['UGV_BASE_AUX_POWER_W'],
        'BATTERY_CAPACITY_WH': 9000.0,
        'BATTERY_RESERVE_FRACTION': 0.2,
        'CHARGER_DC_POWER_W': 1500.0,
        'ALPHA_ENERGY': 1.0, 'BETA_TIME': 1.0, 'SOC_COST_WEIGHT': 0.0,
        'DISPATCH_WINDOW_S': 30.0, 'DISPATCH_URGENT_SLACK_S': 60.0,
    }
    changed = variant['changed_parameter']
    if changed in ('UGV_C_RR', 'UGV_ETA', 'UGV_EMPTY_MASS_KG'):
        expected[changed] = float(variant['new_value'])
    if changed == 'UGV_BASE_AUX_POWER_W':
        expected['UGV_DRIVING_AUX_POWER_W'] = float(variant['new_value'])
        expected['UGV_IDLE_POWER_W'] = float(variant['new_value'])
    for field, value in expected.items():
        assert abs(num(p, field) - value) < 1e-7, f'Mismatch {field}: {p.get(field)} vs {value}'

    requests = csvrows(archive / 'request_results.csv')
    vehicles = csvrows(archive / 'vehicle_results.csv')
    canonical = csvrows(archive / 'canonical_requests.csv')
    snapshots = csvrows(archive / 'vehicle_soc_snapshots.csv')
    charge = csvrows(archive / 'charging_events.csv')
    dispatch = csvrows(archive / 'dispatch_invocation_events.csv')
    summary_rows = csvrows(archive / 'run_summary.csv')
    assert len(requests) == len(canonical) == request_count
    assert len(vehicles) == fleet_size and len(summary_rows) == 1 and snapshots
    assert len({r['request_id'] for r in requests}) == request_count
    assert {r['request_id'] for r in canonical} == {r['request_id'] for r in requests}
    assert len({r['vehicle_id'] for r in vehicles}) == fleet_size
    assert all(int(num(r, 'trial_seed')) == seed and r['algorithm'] == a for r in requests)
    completed = 0
    failed_reasons = {}
    for r in requests:
        flag = int(num(r, 'request_completed'))
        assert flag in (0, 1)
        if flag:
            assert r['completion_status'] == 'completed' and r['pickup_time_s'] and r['dropoff_time_s']
            assert not r['failure_reason']
            completed += 1
        else:
            assert r['completion_status'] == 'uncompleted' and r['failure_reason']
            failed_reasons[r['failure_reason']] = failed_reasons.get(r['failure_reason'], 0) + 1
    total = summary_rows[0]
    assert int(num(total, 'spawned_requests')) == request_count
    assert int(num(total, 'completed_requests')) == completed
    assert int(num(total, 'uncompleted_requests')) == request_count - completed
    initial = {}
    for r in snapshots:
        vid = r['vehicle_id']
        start = num(r, 'initial_SOC_Wh')
        assert abs(start - 9000.0) < 0.01
        bal = start + num(r, 'energy_charged_Wh') - num(r, 'energy_consumed_Wh')
        assert abs(num(r, 'SOC_Wh') - bal) < 0.01
        assert 1799.999 <= num(r, 'SOC_Wh') <= 9000.001
        initial[vid] = start
    assert len(initial) == fleet_size
    for r in vehicles:
        assert r['vehicle_id'] in initial
        predicted = initial[r['vehicle_id']] + num(r, 'energy_charged_Wh') - num(r, 'energy_consumed_Wh')
        assert abs(predicted - num(r, 'final_SOC_Wh')) < 0.01
        assert num(r, 'minimum_SOC_Wh') >= 1799.999
    battery_wh = sum(num(r, 'energy_consumed_Wh') for r in vehicles)
    assert abs(battery_wh - num(total, 'total_energy_consumed_Wh')) < 0.05
    stored = sum(num(r, 'energy_charged_Wh') for r in vehicles)
    charged = sum(num(r, 'energy_charged_Wh') for r in charge)
    parked = sum(num(r, 'parked_load_energy_Wh') for r in charge)
    grid = sum(1000 * num(r, 'grid_energy_kWh') for r in charge)
    expected_grid = (parked + stored / num(p, 'BATTERY_CHARGE_EFFICIENCY')) / num(p, 'CHARGER_EFFICIENCY')
    assert abs(stored - charged) < 0.05 and abs(grid - expected_grid) < 0.05
    assert abs(grid - num(total, 'total_grid_import_Wh')) < 0.05
    latencies = [num(r, 'complete_dispatch_ms') for r in dispatch]
    misses = sum(x > 300 for x in latencies)
    assert int(num(total, 'dispatch_count')) == len(latencies)
    assert int(num(total, 'dispatch_budget_miss_count')) == misses
    wait = total.get('mean_deadline_limited_pickup_wait_s', '')
    capped_wait = None if wait in (None, '') else float(wait)
    if capped_wait is not None:
        assert math.isfinite(capped_wait)
    return dict(archive=str(archive), source_sha256=variant['sha256'], completed=completed,
                uncompleted=request_count - completed, failure_reasons=failed_reasons,
                battery_Wh=battery_wh, grid_Wh=grid, capped_wait_s=capped_wait,
                dispatch_count=len(latencies), budget_miss_count=misses,
                canonical_sha256=sha(archive / 'canonical_requests.csv'),
                archive_checksums={filename: sha(archive / filename) for filename in ARCHIVE_FILES})

run_env = dict(os.environ)
run_env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONHASHSEED='0',
               V2_DEPOT_LAYOUT='three', V2_THREE_DEPOT_IDS='P02,P08,P16')
failure = None
try:
    for spec in specs:
        k = key(spec)
        if k in existing:
            continue
        v, s, a, t = k
        label = f'{v}_{s}_{a}_t{t}'
        wd = WORK / label
        if wd.exists():
            raise RuntimeError(f'Unregistered/interrupted directory: {wd}; inspect before resuming')
        rec = dict(**spec, status='STARTED', workdir=str(wd), started_utc=utc())
        progress['records'].append(rec)
        atomic_json(REPORT, progress)
        print(f'\n===== SENSITIVITY {len(progress["records"])}/56: {label} =====', flush=True)
        try:
            shutil.copytree(SOURCE, wd)
            assert sha(wd / 'app.py') == BASE_SOURCE_SHA
            code = variant_codes[v]['source']
            if v != 'baseline':
                (wd / 'app.py').write_text(code, encoding='utf-8')
            assert sha(wd / 'app.py') == variant_codes[v]['sha256']
            cmd = [sys.executable, 'app.py', '--headless', '--scenario', s, '--algorithm', a,
                   '--trial-index', str(t), '--max-sim-s', '14400', '--flush-every', '200',
                   '--depot-layout', 'three', '--dispatch-window-s', '30',
                   '--dispatch-urgent-slack-s', '60', '--initial-soc-range', '1.0', '1.0',
                   '--alpha-energy', '1', '--beta-time', '1', '--soc-cost-weight', '0',
                   '--cost-mode', 'normalized', '--energy-reference-wh', '100',
                   '--time-reference-s', '600']
            proc = subprocess.run(cmd, cwd=wd, env=run_env, capture_output=True, text=True,
                                  timeout=900, check=False)
            (wd / 'step111b_stdout.txt').write_text(proc.stdout, encoding='utf-8')
            (wd / 'step111b_stderr.txt').write_text(proc.stderr, encoding='utf-8')
            assert proc.returncode == 0, f'Process exit {proc.returncode}: {proc.stderr[-1100:]}'
            status = final_status(proc.stdout)
            assert status['status'] == 'completed' and status['error'] is None
            assert (status['scenario'], status['algorithm'], int(status['trial_index']),
                    int(status['trial_seed'])) == (s, a, t, 1001 + 1000 * t)
            archive = Path(status['results_dir']).resolve()
            assert archive.parent == (wd / 'results').resolve()
            metrics = verify(archive, spec)
            wk = (s, t)
            assert wk not in workloads or workloads[wk] == metrics['canonical_sha256'], 'Paired workload mismatch'
            workloads[wk] = metrics['canonical_sha256']
            rec.update(metrics, status='VALIDATED', completed_utc=utc())
            existing[k] = rec
            atomic_json(REPORT, progress)
            print(f'PASS: {metrics["completed"]}/{SIZES[s][1]} completed | '
                  f'{metrics["battery_Wh"]:.2f} Wh | >300ms={metrics["budget_miss_count"]}', flush=True)
        except Exception as exc:
            rec['status'] = 'FAILED'
            rec['error'] = f'{type(exc).__name__}: {exc}'
            atomic_json(REPORT, progress)
            failure = rec['error']
            print('STOP:', failure, flush=True)
            break
finally:
    print('\n===== SOURCE INTEGRITY =====', flush=True)
    check_protected()
    print('PASS: FIX-19, FIX-20, frozen package and main manifest unchanged', flush=True)
    print('Progress report:', REPORT, flush=True)

validated = sum(x['status'] == 'VALIDATED' for x in progress['records'])
print(f'\nSTEP 111B validated: {validated}/56')
if not failure and validated == 56:
    comparison = []
    for s in SCENARIOS:
        for a in ALGORITHMS:
            for v, changed, value in VARIANTS[1:]:
                ds = []
                de = []
                dc = []
                dg = []
                for t in TRIALS:
                    base = existing[('baseline', s, a, t)]
                    perturbed = existing[(v, s, a, t)]
                    de.append(perturbed['battery_Wh'] - base['battery_Wh'])
                    dc.append(perturbed['completed'] - base['completed'])
                    dg.append(perturbed['grid_Wh'] - base['grid_Wh'])
                    if base['capped_wait_s'] is not None and perturbed['capped_wait_s'] is not None:
                        ds.append(perturbed['capped_wait_s'] - base['capped_wait_s'])
                entry = dict(scenario=s, algorithm=a, variant=v, changed_parameter=changed,
                             value=value, seeds=len(TRIALS), mean_delta_battery_Wh=sum(de)/len(de),
                             min_delta_battery_Wh=min(de), max_delta_battery_Wh=max(de),
                             mean_delta_completed=sum(dc)/len(dc),
                             mean_delta_grid_Wh=sum(dg)/len(dg),
                             mean_delta_capped_wait_s=(sum(ds)/len(ds) if len(ds)==len(TRIALS) else ''),
                             baseline_mean_battery_Wh=sum(existing[('baseline', s, a, t)]['battery_Wh'] for t in TRIALS)/len(TRIALS))
                comparison.append(entry)
    with (WORK / 'step111b_exploratory_sensitivity.csv').open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(comparison[0]))
        writer.writeheader()
        writer.writerows(comparison)
    print('\n===== STEP 111B: MEAN CHANGE RELATIVE TO BASELINE (2 SEEDS) =====')
    print(f'{"Scenario":8}{"Algorithm":12}{"Variant":20}{"dBattery Wh":14}{"dCompleted":12}{"dWait s":10}')
    for rec in comparison:
        w = rec['mean_delta_capped_wait_s']
        print(f'{rec["scenario"]:8}{rec["algorithm"]:12}{rec["variant"]:20}'
              f'{rec["mean_delta_battery_Wh"]:+13.2f} {rec["mean_delta_completed"]:+11.2f} '
              f'{w if w == "" else f"{w:+.2f}"}')
    print('PASS: 56/56 isolated sensitivity simulations validated')
    print('Comparison CSV:', WORK / 'step111b_exploratory_sensitivity.csv')
    print('WARNING: Illustrative sensitivity does NOT establish physical vehicle accuracy.')
else:
    print('STOPPED: Preserve all files; do not overwrite any interrupted/failed run.')
print('===== STEP 111B COMPLETE =====')
