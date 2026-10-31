#!/usr/bin/env python3
"""FIX-19 / STEP 110C: pilot-informed energy-weight ablation holdout.

144 cases: 2 scenarios x 2 algorithms x 1 SOC x 2 alpha x 18 new trial seeds.
Pilot trials 0 and 1 are excluded from holdout inference.
This is simulation-level evidence, not physical validation or a hard real-time guarantee.
"""
from pathlib import Path
from importlib import metadata
from datetime import datetime, timezone
import csv
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import sys

ROOT = Path('research_revision_v2').resolve()
FREEZE = ROOT / 'step106b_frozen_candidate_20261029T174020Z'
SOURCE = FREEZE / 'simulator'
WORK = ROOT / 'step110c_holdout_alpha_ablation'
REPORT = WORK / 'holdout_progress.json'
SOURCE_SHA = '7b52bb550414469937da3098f91313b366137d43bd48b8465b840a97bffeb0a0'
EXPECTED = {
    ROOT / 'fix19_candidate/app.py': SOURCE_SHA,
    ROOT / 'fix20_candidate/app.py': '1ae0e683ae4d6f7839841c5bf2123770e8d86482819679a7f533d465d920153d',
    SOURCE / 'app.py': SOURCE_SHA,
    SOURCE / 'utp_graph.graphml': 'ebd138a83763f11172e079f40457b371be7eebcd2e3aa018aeda13d9d70114f4',
    SOURCE / 'hotspot_locations.csv': '6a55a275f2ac18ab383d2efcd67a31250602503a62fa3c5bf2f3f504ae0b979d',
    FREEZE / 'campaign_640_manifest.csv': '33361ce7a27c547cfa0dbb1a1fd37506b75d263d3ccf2334805428d545096189',
    FREEZE / 'frozen_bundle.zip': 'f44c29293608268eae77ab5c4fe1a5a890e0671af78cd0ac100ccae8af04683b',
}
SCENARIOS = ('S3', 'S4')
ALGORITHMS = ('hungarian', 'greedy')
SOC_LEVELS = (1.0,)
ALPHAS = (0.0, 1.0)
TRIALS = tuple(range(2, 20))
SIZES = {'S3': (24, 100), 'S4': (12, 100)}
FILES = (
    'headless_status.json', 'run_settings.json', 'canonical_requests.csv',
    'request_results.csv', 'vehicle_results.csv', 'run_summary.csv',
    'vehicle_soc_snapshots.csv', 'charging_events.csv',
    'assignment_events.csv', 'dispatch_invocation_events.csv',
    'timing.csv', 'backend_source.py', 'requirements-freeze.txt',
)

def sha(path):
    assert path.is_file() and not path.is_symlink(), f'File absent or symlink: {path}'
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()

def rows(path):
    assert path.is_file(), f'Missing CSV {path}'
    with path.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames, f'Missing CSV header: {path}'
        return list(reader)

def num(row, k):
    v = float(row[k])
    assert math.isfinite(v), f'Invalid {k}'
    return v

def key(obj):
    return (obj['scenario'], obj['algorithm'], int(obj['trial_index']),
            float(obj['initial_soc']), float(obj['alpha_energy']))

def pairkey(obj):
    return (obj['scenario'], obj['algorithm'], int(obj['trial_index']),
            float(obj['initial_soc']))

def utc():
    return datetime.now(timezone.utc).isoformat()

def protected():
    for path, expected in EXPECTED.items():
        assert sha(path) == expected, f'PROTECTED FILE CHANGED: {path}'
    for name, digest in freeze_hashes.items():
        assert sha(FREEZE / name) == digest, f'FROZEN FILE CHANGED: {name}'

def checkpoint():
    temp = REPORT.with_suffix('.json.tmp')
    temp.write_text(json.dumps(progress, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    os.replace(temp, REPORT)

def final_status(output):
    decoder = json.JSONDecoder()
    for i in range(len(output) - 1, -1, -1):
        if output[i] != '{':
            continue
        try:
            d, _ = decoder.raw_decode(output[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and 'results_dir' in d:
            return d
    raise RuntimeError('No final headless status JSON in stdout')

def verify(archive, spec):
    for name in FILES:
        assert (archive / name).is_file(), f'Missing archive file: {name}'
    s, a, t, soc, alpha = key(spec)
    seed = 1001 + t * 1000
    fleet_size, request_count = SIZES[s]
    status = json.loads((archive/'headless_status.json').read_text())
    settings = json.loads((archive/'run_settings.json').read_text())
    p = settings['parameters']
    assert status['status'] == 'completed' and status['error'] is None
    assert status['run_id'] == archive.name
    assert (status['scenario'], status['algorithm'], int(status['trial_index']),
            int(status['trial_seed'])) == (s, a, t, seed)
    assert (settings['scenario'], settings['algorithm'], int(settings['trial_index']),
            int(settings['configured_seeds']['TRIAL_SEED'])) == (s, a, t, seed)
    assert settings['run_id'] == archive.name
    assert settings['backend_source']['sha256'] == SOURCE_SHA
    assert sha(archive / 'backend_source.py') == SOURCE_SHA
    assert sha(archive/'requirements-freeze.txt') == settings['environment']['package_snapshot']['sha256']
    assert settings['depot_layout'] == 'three'
    assert settings['three_depot_selection']['parking_ids'] == ['P02','P08','P16']
    assert settings['scenario_config']['initial_soc_range'] == [soc, soc]
    assert len(settings['initial_fleet']) == fleet_size
    assert all(abs(num(x, 'soc_Wh') - 9000*soc) < 1e-6 for x in settings['initial_fleet'])
    assert p['UGV_HVAC_MODE'] == 'off'
    assert p['ASSIGNMENT_COST_MODE'] == 'normalized'
    for k, v in (
        ('BATTERY_CAPACITY_WH', 9000), ('BATTERY_RESERVE_FRACTION', .2),
        ('ALPHA_ENERGY', alpha), ('BETA_TIME', 1), ('SOC_COST_WEIGHT', 0),
        ('CHARGER_DC_POWER_W', 1500), ('DISPATCH_WINDOW_S', 30),
        ('DISPATCH_URGENT_SLACK_S', 60), ('COST_ENERGY_REFERENCE_WH', 100),
        ('COST_TIME_REFERENCE_S', 600),
    ):
        assert abs(num(p, k)-v) < 1e-8, f'Parameter mismatch {k}'

    canonical = rows(archive/'canonical_requests.csv')
    requests = rows(archive/'request_results.csv')
    vehicles = rows(archive/'vehicle_results.csv')
    summaries = rows(archive/'run_summary.csv')
    snapshots = rows(archive/'vehicle_soc_snapshots.csv')
    charge = rows(archive/'charging_events.csv')
    dispatch = rows(archive/'dispatch_invocation_events.csv')
    timing = rows(archive/'timing.csv')
    assignments = rows(archive/'assignment_events.csv')
    assert len(canonical) == len(requests) == request_count
    assert len(vehicles) == fleet_size and len(summaries) == 1
    assert snapshots
    assert len({r['request_id'] for r in canonical}) == request_count
    assert {r['request_id'] for r in canonical} == {r['request_id'] for r in requests}
    assert len({r['vehicle_id'] for r in vehicles}) == fleet_size
    assert sha(archive/'canonical_requests.csv') == settings['canonical_workload']['sha256']
    complete = 0
    failure_reasons = {}
    for r in requests:
        assert (r['scenario'], r['algorithm'], int(num(r,'trial_seed')),
                int(num(r,'trial_index'))) == (s,a,seed,t)
        flag = int(num(r,'request_completed'))
        assert flag in (0,1)
        if flag:
            assert r['completion_status'] == 'completed' and r['pickup_time_s'] and r['dropoff_time_s']
            assert not r['failure_reason']
            complete += 1
        else:
            assert r['completion_status'] == 'uncompleted' and r['failure_reason']
            failure_reasons[r['failure_reason']] = failure_reasons.get(r['failure_reason'], 0)+1
    summary = summaries[0]
    assert int(num(summary,'spawned_requests')) == request_count
    assert int(num(summary,'completed_requests')) == complete
    assert int(num(summary,'uncompleted_requests')) == request_count - complete
    initial = {}
    for r in snapshots:
        vid = r['vehicle_id']
        start = num(r,'initial_SOC_Wh')
        assert abs(start - 9000*soc) < .01
        battery_balance = start + num(r,'energy_charged_Wh') - num(r,'energy_consumed_Wh')
        assert abs(num(r,'SOC_Wh') - battery_balance) < .01
        assert 1799.999 <= num(r,'SOC_Wh') <= 9000.001
        initial[vid] = start
    assert len(initial) == fleet_size
    for r in vehicles:
        vid = r['vehicle_id']
        assert vid in initial
        final = initial[vid] + num(r,'energy_charged_Wh') - num(r,'energy_consumed_Wh')
        assert abs(final - num(r,'final_SOC_Wh')) < .01
        assert num(r,'minimum_SOC_Wh') >= 1799.999
    stored = sum(num(r,'energy_charged_Wh') for r in vehicles)
    charged = sum(num(r,'energy_charged_Wh') for r in charge)
    grid = sum(1000*num(r,'grid_energy_kWh') for r in charge)
    parked = sum(num(r,'parked_load_energy_Wh') for r in charge)
    grid_reconstructed = (parked + stored/num(p,'BATTERY_CHARGE_EFFICIENCY')) / num(p,'CHARGER_EFFICIENCY')
    assert abs(stored-charged) < .05
    assert abs(grid-grid_reconstructed) < .05
    battery_wh = sum(num(r,'energy_consumed_Wh') for r in vehicles)
    assert abs(battery_wh-num(summary,'total_energy_consumed_Wh')) < .05
    assert abs(grid-num(summary,'total_grid_import_Wh')) < .05
    assert all(r['algorithm']==a and int(num(r,'trial_seed'))==seed for r in assignments)
    times = [num(r,'complete_dispatch_ms') for r in dispatch]
    gaps = [num(r,'matching_cardinality_gap') for r in timing]
    assert all(v >= 0 for v in times + gaps)
    misses = sum(v > 300 for v in times)
    assert int(num(summary,'dispatch_count')) == len(times)
    assert int(num(summary,'dispatch_budget_miss_count')) == misses
    wait = summary.get('mean_deadline_limited_pickup_wait_s', '')
    capped_wait = None if wait in ('',None) else float(wait)
    if capped_wait is not None: assert math.isfinite(capped_wait)
    return dict(archive=str(archive), completed=complete, uncompleted=request_count-complete,
                battery_Wh=battery_wh, grid_Wh=grid, capped_wait_s=capped_wait,
                dispatch_count=len(times), budget_miss_count=misses, dispatch_max_ms=max(times, default=None),
                energy_blocked_pair_evaluations=int(status.get('energy_blocked_pair_evaluations') or 0),
                energy_blocked_insertions=int(status.get('energy_blocked_insertions') or 0),
                failure_reasons=failure_reasons, canonical_sha256=sha(archive/'canonical_requests.csv'),
                initial_fleet=settings['initial_fleet'],
                archive_checksums={name: sha(archive/name) for name in FILES})

print('===== FIX-19 / STEP 110C: ENERGY-WEIGHT ABLATION HOLDOUT =====', flush=True)
freeze_hashes = {}
for line in (FREEZE/'SHA256SUMS.txt').read_text().splitlines():
    digest, name = line.split('  ',1)
    freeze_hashes[name] = digest
protected()
record = json.loads((FREEZE/'freeze_record.json').read_text())
assert record['freeze_status'] == 'FROZEN_CANDIDATE' and record['experiment_count'] == 640
for name, digest in record['source_tree'].items():
    assert sha(SOURCE/name)==digest
snapshot = json.loads((FREEZE/'environment.json').read_text())
assert sys.version == snapshot['python'] and platform.platform() == snapshot['platform']
for name, version in snapshot['packages'].items():
    assert metadata.version(name) == version
print('PASS: Protected benchmark source, graph, manifest and environment', flush=True)

cases = [dict(scenario=s, algorithm=a, trial_index=t, initial_soc=soc, alpha_energy=alpha)
         for s in SCENARIOS for a in ALGORITHMS for soc in SOC_LEVELS
         for t in TRIALS for alpha in ALPHAS]
assert len(cases) == 144 and len({key(x) for x in cases}) == 144
protocol = dict(
    protocol_id='step110c_holdout_v1', stage='PILOT_INFORMED_HOLDOUT_18_UNEXAMINED_ALPHA0_SEEDS',
    frozen_source_sha256=SOURCE_SHA, paired_seed_indices=list(TRIALS),
    paired_seeds=[1001 + i*1000 for i in TRIALS], scenarios=list(SCENARIOS), algorithms=list(ALGORITHMS),
    initial_soc_levels=list(SOC_LEVELS), alpha_energy_levels=list(ALPHAS),
    common_parameters=dict(beta_time=1, gamma_soc=0, cost_mode='normalized',
                           energy_reference_Wh=100, time_reference_s=600,
                           battery_capacity_Wh=9000, reserve_fraction=.2,
                           charging_power_W=1500, HVAC='off',
                           depot_layout='three', depot_ids=['P02','P08','P16'],
                           dispatch_window_s=30, urgent_slack_s=60),
    main_question='Controlled within-simulator effect of explicit energy-cost weighting alpha=1 vs alpha=0, with energy feasibility retained in both arms.',
    primary_outcomes=['completed_requests','battery_Wh','capped_wait_s'],
    secondary_outcomes=['energy_blocked_pair_evaluations','energy_blocked_insertions',
                        'budget_miss_count','dispatch_max_ms','grid_Wh'],
    scientific_limits=['Trial indices 0 and 1 were examined during the pilot and are excluded here.',
                       'Holdout cases use 100% initial SOC. This isolates the energy-cost term only, NOT the feasibility filter.',
                       'No real-vehicle energy validation or worst-case real-time guarantee.'],
    cases=cases,
)
WORK.mkdir(exist_ok=True)
protocol_path = WORK/'holdout_protocol.json'
if protocol_path.exists():
    assert json.loads(protocol_path.read_text()) == protocol, 'Existing holdout protocol differs; stop'
else:
    assert not any(WORK.iterdir()), 'Unregistered work directory contents; stop'
    protocol_path.write_text(json.dumps(protocol, indent=2, allow_nan=False)+'\n', encoding='utf-8')

if REPORT.exists():
    progress = json.loads(REPORT.read_text())
    assert progress['protocol_sha256'] == sha(protocol_path)
else:
    assert {p.name for p in WORK.iterdir()} == {'holdout_protocol.json'}, 'Unregistered files before initialization'
    progress = dict(protocol_sha256=sha(protocol_path), planned=144,
                    created_utc=utc(), records=[])
    checkpoint()

by_key = {}
paired_workloads = {}
paired_fleets = {}
for old in progress['records']:
    k = key(old)
    assert k not in by_key and k in {key(x) for x in cases}
    by_key[k] = old
    assert old['status'] == 'VALIDATED', f'Interrupted/unvalidated holdout case {k}; stop, inspect before rerun'
    archive = Path(old['archive']).resolve()
    assert archive.is_relative_to(WORK) and archive.is_dir()
    for name, digest in old['archive_checksums'].items():
        assert sha(archive/name) == digest
    wk = (old['scenario'], int(old['trial_index']))
    if wk in paired_workloads:
        assert paired_workloads[wk] == old['canonical_sha256']
    paired_workloads[wk] = old['canonical_sha256']
    fk = (old['scenario'],old['algorithm'],int(old['trial_index']),float(old['initial_soc']))
    if fk in paired_fleets:
        assert paired_fleets[fk] == old['initial_fleet']
    paired_fleets[fk] = old['initial_fleet']
print(f'Holdout validated already: {len(by_key)}/144; remaining: {144-len(by_key)}', flush=True)

run_env=os.environ.copy()
run_env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONHASHSEED='0',
               V2_DEPOT_LAYOUT='three',V2_THREE_DEPOT_IDS='P02,P08,P16')
error = None
try:
    for spec in cases:
        k = key(spec)
        if k in by_key: continue
        s,a,t,soc,alpha = k
        label = f'{s}_{a}_t{t}_soc{int(soc*100)}_alpha{int(alpha)}'
        wd = WORK / label
        if wd.exists():
            raise RuntimeError(f'Unregistered experiment directory: {wd}; stop')
        entry = dict(**spec, status='STARTED', workdir=str(wd), started_utc=utc())
        progress['records'].append(entry)
        checkpoint()
        print(f'\n===== HOLDOUT {len(progress["records"])}/144: {label} =====',flush=True)
        try:
            shutil.copytree(SOURCE,wd)
            assert sha(wd/'app.py') == SOURCE_SHA
            cmd=[sys.executable,'app.py','--headless','--scenario',s,
                 '--algorithm',a,'--trial-index',str(t),'--max-sim-s','14400',
                 '--flush-every','200','--depot-layout','three',
                 '--dispatch-window-s','30','--dispatch-urgent-slack-s','60',
                 '--initial-soc-range',str(soc),str(soc),
                 '--alpha-energy',str(alpha),'--beta-time','1',
                 '--soc-cost-weight','0','--cost-mode','normalized',
                 '--energy-reference-wh','100','--time-reference-s','600']
            process=subprocess.run(cmd,cwd=wd,env=run_env,capture_output=True,
                                   text=True,timeout=900,check=False)
            (wd/'holdout_stdout.txt').write_text(process.stdout,encoding='utf-8')
            (wd/'holdout_stderr.txt').write_text(process.stderr,encoding='utf-8')
            assert process.returncode==0, f'Exit {process.returncode}: {process.stderr[-1100:]}'
            st=final_status(process.stdout)
            assert st['status']=='completed' and st['error'] is None
            assert (st['scenario'],st['algorithm'],int(st['trial_index']),int(st['trial_seed']))==(s,a,t,1001+t*1000)
            archive=Path(st['results_dir']).resolve()
            assert archive.parent == (wd/'results').resolve()
            metrics=verify(archive,spec)
            wk=(s,t)
            assert wk not in paired_workloads or paired_workloads[wk] == metrics['canonical_sha256'], 'Paired workload mismatch'
            paired_workloads[wk]=metrics['canonical_sha256']
            fk=(s,a,t,soc)
            assert fk not in paired_fleets or paired_fleets[fk] == metrics['initial_fleet'], 'Paired SOC mismatch'
            paired_fleets[fk]=metrics['initial_fleet']
            entry.update(metrics)
            entry['status']='VALIDATED'
            entry['completed_utc']=utc()
            by_key[k]=entry
            checkpoint()
            print(f'PASS: {metrics["completed"]}/{SIZES[s][1]} completed | {metrics["battery_Wh"]:.2f} Wh | '
                  f'energy-blocked pairs={metrics["energy_blocked_pair_evaluations"]} | >300ms={metrics["budget_miss_count"]}',flush=True)
        except Exception as exc:
            entry['status']='FAILED'
            entry['error']=f'{type(exc).__name__}: {exc}'
            checkpoint()
            error=entry['error']
            print('STOP:',error,flush=True)
            break
finally:
    print('\n===== PROTECTED SOURCE INTEGRITY =====', flush=True)
    protected()
    print('PASS: FIX-19/FIX-20, frozen source, original 640-manifest UNCHANGED', flush=True)
    print('Progress report:', REPORT,flush=True)

if not error and len(by_key)==144:
    paired=[]
    for s in SCENARIOS:
        for a in ALGORITHMS:
            for soc in SOC_LEVELS:
                for t in TRIALS:
                    zero=by_key[(s,a,t,soc,0.)]
                    one=by_key[(s,a,t,soc,1.)]
                    paired.append(dict(
                        scenario=s,algorithm=a,trial_seed=1001+t*1000,initial_soc=soc,
                        alpha0_completed=zero['completed'],alpha1_completed=one['completed'],
                        alpha1_minus_alpha0_battery_Wh=round(one['battery_Wh']-zero['battery_Wh'],6),
                        alpha1_minus_alpha0_completed=one['completed']-zero['completed'],
                        alpha1_minus_alpha0_capped_wait_s=(
                            None if zero['capped_wait_s'] is None or one['capped_wait_s'] is None
                            else round(one['capped_wait_s']-zero['capped_wait_s'],6)
                        ),
                        alpha0_energy_blocked_pairs=zero['energy_blocked_pair_evaluations'],
                        alpha1_energy_blocked_pairs=one['energy_blocked_pair_evaluations']
                    ))
    output=WORK/'paired_alpha1_minus_alpha0_holdout.csv'
    with output.open('w', newline='', encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(paired[0]))
        w.writeheader();w.writerows(paired)
    print('\n===== HOLDOUT COMPARISONS (alpha1 - alpha0) =====',flush=True)
    for item in paired:
        print(f'{item["scenario"]:2} {item["algorithm"]:10} '
              f'soc={item["initial_soc"]:.1f} seed={item["trial_seed"]} '
              f'dEnergy={item["alpha1_minus_alpha0_battery_Wh"]:+.3f} Wh '
              f'dCompleted={item["alpha1_minus_alpha0_completed"]:+d} '
              f'dCappedWait={item["alpha1_minus_alpha0_capped_wait_s"]}',flush=True)
    print('\n===== HOLDOUT SUMMARY: 18 INDEPENDENT SEEDS PER CONFIGURATION =====',flush=True)
    for scenario in SCENARIOS:
        for algorithm in ALGORITHMS:
            subset = [r for r in paired if r['scenario']==scenario and r['algorithm']==algorithm]
            assert len(subset)==18 and all(r['initial_soc']==1.0 for r in subset)
            d = [r['alpha1_minus_alpha0_battery_Wh'] for r in subset]
            dc = [r['alpha1_minus_alpha0_completed'] for r in subset]
            dw = [r['alpha1_minus_alpha0_capped_wait_s'] for r in subset]
            assert all(x is not None for x in dw)
            print(f'{scenario} {algorithm:10}: mean dEnergy={sum(d)/18:+.3f} Wh; '
                  f'savings seeds={sum(x < -1e-6 for x in d)}/18; '
                  f'higher energy seeds={sum(x > 1e-6 for x in d)}/18; '
                  f'mean dWait={sum(dw)/18:+.3f} s; '
                  f'mean dCompleted={sum(dc)/18:+.3f}',flush=True)
    print('NOTE: Negative dEnergy means alpha=1 saved simulated battery withdrawal.',flush=True)
    print('NOTE: These 18 holdout trials exclude pilot seeds 1001 and 2001; no statistical significance claimed yet.',flush=True)
    print('PASS: 144/144 holdout runs validated; 72 paired contrasts; no main-campaign modifications',flush=True)
    print('Paired results:', output,flush=True)
else:
    print(f'STOPPED: {len(by_key)}/144 validated holdout runs; preserve all evidence',flush=True)
print('===== STEP 110C COMPLETE =====',flush=True)
