#!/usr/bin/env python3
"""FIX-24 / STEP 117A: preregistered S5 low-SOC diagnostic pilot.

S5 is a SUPPLEMENTARY EXPERIMENT LABEL, executed with the immutable FIX-19
S4 (12 UGV / 100-request) workload using --initial-soc-range.  The accepted
S1-S4 640-run campaign and the physical model are NEVER changed.

Protocol: 2 algorithms x 2 paired seeds x 3 SOC levels = 12 isolated runs.
SOC=0.30 reference, 0.25 proposed S5, 0.22 severe diagnostic (not an
operating recommendation). Do NOT extrapolate significance from two seeds.

Run from ~/utp-uber-app using the archived Python virtual environment.
"""
import csv
import hashlib
import io
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
from zipfile import ZipFile

ROOT = Path('research_revision_v2').resolve()
FREEZE = ROOT / 'step106b_frozen_candidate_20261029T174020Z'
SOURCE = FREEZE / 'simulator'
RELEASE = ROOT / 'step115_reproducibility_release' / 'step115_compact_evidence_release.zip'
WORK = ROOT / 'step117a_s5_battery_stress_pilot'
PROTOCOL = WORK / 'step117a_protocol.json'
PROGRESS = WORK / 'step117a_progress.json'
RESULTS = WORK / 'step117a_summary.csv'
SOURCE_SHA = '7b52bb550414469937da3098f91313b366137d43bd48b8465b840a97bffeb0a0'
PROTECTED = {
    ROOT / 'fix19_candidate/app.py': SOURCE_SHA,
    ROOT / 'fix20_candidate/app.py': '1ae0e683ae4d6f7839841c5bf2123770e8d86482819679a7f533d465d920153d',
    ROOT / 'step113a_fix21_candidate_pilot/fix21_source_copy/app.py': '2e466def64d70a710fa43a77d9d4c1d3229a6c8cff5a080777e9309f5c303e15',
    ROOT / 'step113c_fix21_geometry_candidate_pilot/fix21_geometry_source_copy/app.py': '518eeb94dd2e794d9d6b8460de8e94332cb6ef8a1da75d3142b262cd43d62975',
    SOURCE / 'app.py': SOURCE_SHA,
    SOURCE / 'utp_graph.graphml': 'ebd138a83763f11172e079f40457b371be7eebcd2e3aa018aeda13d9d70114f4',
    SOURCE / 'hotspot_locations.csv': '6a55a275f2ac18ab383d2efcd67a31250602503a62fa3c5bf2f3f504ae0b979d',
    FREEZE / 'campaign_640_manifest.csv': '33361ce7a27c547cfa0dbb1a1fd37506b75d263d3ccf2334805428d545096189',
    FREEZE / 'frozen_bundle.zip': 'f44c29293608268eae77ab5c4fe1a5a890e0671af78cd0ac100ccae8af04683b',
}
SOC_LEVELS = (0.30, 0.25, 0.22)
ALGORITHMS = ('hungarian', 'greedy')
TRIAL_INDICES = (0, 1)
ARCHIVE_FILES = (
    'headless_status.json', 'run_settings.json', 'canonical_requests.csv',
    'request_results.csv', 'vehicle_results.csv', 'run_summary.csv',
    'vehicle_soc_snapshots.csv', 'charging_events.csv',
    'assignment_events.csv', 'dispatch_invocation_events.csv',
    'timing.csv', 'backend_source.py', 'requirements-freeze.txt',
)


def sha(path):
    p = Path(path)
    if not p.is_file() or p.is_symlink():
        raise AssertionError(f'Missing file or symlink: {p}')
    h = hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def sha_bytes(content):
    return hashlib.sha256(content).hexdigest()


def rows(path):
    p = Path(path)
    assert p.is_file(), f'CSV not found: {p}'
    with p.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames, f'CSV header missing: {p}'
        return list(reader)


def rows_bytes(content):
    with io.TextIOWrapper(io.BytesIO(content), encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def num(row, field):
    n = float(row[field])
    assert math.isfinite(n), f'Non-finite value in {field}: {row[field]!r}'
    return n


def utc():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, obj):
    tmp = path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(obj, indent=2, allow_nan=False, sort_keys=True)+'\n', encoding='utf-8')
    os.replace(tmp, path)


def key(rec):
    return (rec['algorithm'], int(rec['trial_index']), float(rec['soc']))


def final_status(output):
    decoder = json.JSONDecoder()
    for i in range(len(output)-1, -1, -1):
        if output[i] != '{':
            continue
        try:
            data, _ = decoder.raw_decode(output[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and 'results_dir' in data:
            return data
    raise RuntimeError('No final status JSON in stdout')


def check_protected():
    for path, expected in PROTECTED.items():
        assert sha(path) == expected, f'STOP: protected hash changed: {path}'
    for name, expected in freeze_inventory.items():
        assert sha(FREEZE / name) == expected, f'STOP: frozen inventory changed: {name}'
    assert sha(RELEASE) == release_zip_sha, 'STOP: Step115 evidence ZIP changed during test'
    if original_campaign_state is not None:
        assert sha(original_campaign_file) == original_campaign_state, 'STOP: accepted campaign progress changed'


def verify_archive(archive, spec, baseline_hashes):
    for name in ARCHIVE_FILES:
        assert (archive/name).is_file(), f'Missing {name}'
    a, t, soc = key(spec)
    seed = 1001+1000*t
    status = json.loads((archive/'headless_status.json').read_text(encoding='utf-8'))
    settings = json.loads((archive/'run_settings.json').read_text(encoding='utf-8'))
    p = settings['parameters']
    assert status['status']=='completed' and status['error'] is None
    assert status['run_id']==archive.name
    assert (status['scenario'],status['algorithm'],int(status['trial_index']),int(status['trial_seed'])) == ('S4',a,t,seed)
    assert (settings['scenario'],settings['algorithm'],int(settings['trial_index']),int(settings['configured_seeds']['TRIAL_SEED'])) == ('S4',a,t,seed)
    assert settings['run_id']==archive.name
    assert settings['backend_source']['sha256']==SOURCE_SHA
    assert sha(archive/'backend_source.py')==SOURCE_SHA
    assert sha(archive/'requirements-freeze.txt')==settings['environment']['package_snapshot']['sha256']
    assert settings['scenario_config']['vehicles'] == 12 and settings['scenario_config']['requests'] == 100
    assert settings['scenario_config']['initial_soc_range']==[soc,soc]
    assert settings['depot_layout']=='three' and settings['three_depot_selection']['parking_ids']==['P02','P08','P16']
    assert p['ASSIGNMENT_COST_MODE']=='normalized' and p['UGV_HVAC_MODE']=='off'
    for field, expected in (
        ('BATTERY_CAPACITY_WH',9000),('BATTERY_RESERVE_FRACTION',0.20),
        ('ALPHA_ENERGY',1),('BETA_TIME',1),('SOC_COST_WEIGHT',0),
        ('CHARGER_DC_POWER_W',1500),('DISPATCH_WINDOW_S',30),
        ('DISPATCH_URGENT_SLACK_S',60),('COST_ENERGY_REFERENCE_WH',100),
        ('COST_TIME_REFERENCE_S',600),
    ):
        assert math.isclose(num(p,field),expected,rel_tol=0,abs_tol=1e-7), field
    assert len(settings['initial_fleet'])==12
    assert all(math.isclose(float(x['soc_Wh']),9000*soc,abs_tol=1e-6) for x in settings['initial_fleet'])
    canonical = rows(archive/'canonical_requests.csv')
    reqs = rows(archive/'request_results.csv')
    vehicles = rows(archive/'vehicle_results.csv')
    charging = rows(archive/'charging_events.csv')
    snapshots = rows(archive/'vehicle_soc_snapshots.csv')
    assigns = rows(archive/'assignment_events.csv')
    summary_rows = rows(archive/'run_summary.csv')
    dispatch = rows(archive/'dispatch_invocation_events.csv')
    assert len(canonical)==len(reqs)==100 and len(vehicles)==12 and len(summary_rows)==1 and snapshots
    assert len({x['vehicle_id'] for x in snapshots})==12
    assert len({x['request_id'] for x in canonical})==100
    assert {x['request_id'] for x in canonical}=={x['request_id'] for x in reqs}
    canonical_hash = sha(archive/'canonical_requests.csv')
    assert settings['canonical_workload']['sha256']==canonical_hash
    assert canonical_hash==baseline_hashes[('S4',t)], 'STOP: workload differs from accepted S4 with same seed'
    complete=sum(int(num(x,'request_completed')) for x in reqs)
    failure_reasons={}
    for r in reqs:
        assert (r['scenario'],r['algorithm'],int(num(r,'trial_seed')),int(num(r,'trial_index'))) == ('S4',a,seed,t)
        flag=int(num(r,'request_completed'))
        assert flag in (0,1)
        if flag:
            assert r['completion_status']=='completed' and not r['failure_reason']
        else:
            assert r['completion_status']=='uncompleted' and r['failure_reason']
            reason=r['failure_reason']
            failure_reasons[reason]=failure_reasons.get(reason,0)+1
    summary=summary_rows[0]
    assert int(num(summary,'spawned_requests'))==100
    assert int(num(summary,'completed_requests'))==complete
    assert int(num(summary,'uncompleted_requests'))==100-complete
    original_initial={x['vehicle_id']:9000*soc for x in vehicles}
    assert len(original_initial)==12
    for r in snapshots:
        v=r['vehicle_id']
        assert v in original_initial
        assert math.isclose(num(r,'initial_SOC_Wh'),original_initial[v],abs_tol=.01)
        expected=num(r,'initial_SOC_Wh') + num(r,'energy_charged_Wh')-num(r,'energy_consumed_Wh')
        assert math.isclose(num(r,'SOC_Wh'),expected,abs_tol=.01)
        assert 1799.999<=num(r,'SOC_Wh')<=9000.001
    for r in vehicles:
        v=r['vehicle_id']
        assert v in original_initial
        expected=original_initial[v]+num(r,'energy_charged_Wh')-num(r,'energy_consumed_Wh')
        assert math.isclose(num(r,'final_SOC_Wh'),expected,abs_tol=.01)
        assert num(r,'minimum_SOC_Wh')>=1799.999, f'Reserve violation for vehicle {v}'
    stored=sum(num(v,'energy_charged_Wh') for v in vehicles)
    charge_stored=sum(num(c,'energy_charged_Wh') for c in charging)
    grid=sum(1000*num(c,'grid_energy_kWh') for c in charging)
    parked=sum(num(c,'parked_load_energy_Wh') for c in charging)
    expected_grid=(parked+stored/num(p,'BATTERY_CHARGE_EFFICIENCY'))/num(p,'CHARGER_EFFICIENCY')
    assert math.isclose(stored,charge_stored,abs_tol=.05)
    assert math.isclose(grid,expected_grid,abs_tol=.05)
    battery=sum(num(v,'energy_consumed_Wh') for v in vehicles)
    assert math.isclose(battery,num(summary,'total_energy_consumed_Wh'),abs_tol=.05)
    assert math.isclose(grid,num(summary,'total_grid_import_Wh'),abs_tol=.05)
    assert all(x['algorithm']==a and int(num(x,'trial_seed'))==seed for x in assigns)
    dispatch_ms=[num(x,'complete_dispatch_ms') for x in dispatch]
    misses=sum(x>300 for x in dispatch_ms)
    assert int(num(summary,'dispatch_count'))==len(dispatch_ms)
    assert int(num(summary,'dispatch_budget_miss_count'))==misses
    blocked_pairs=int(status.get('energy_blocked_pair_evaluations') or 0)
    blocked_insertions=int(status.get('energy_blocked_insertions') or 0)
    assert blocked_pairs>=0 and blocked_insertions>=0
    energy_summary_file=archive/'energy_feasibility_summary.csv'
    if energy_summary_file.is_file():
        es=rows(energy_summary_file)
        assert len(es)==1
        assert int(num(es[0],'energy_blocked_pair_evaluations'))==blocked_pairs
        assert int(num(es[0],'energy_blocked_insertions'))==blocked_insertions
    if blocked_pairs:
        assert blocked_insertions>0, 'Energy-blocked pair without rejected insertion'
    metrics = dict(
        archive=str(archive), canonical_workload_sha256=canonical_hash,
        completed=complete, uncompleted=100-complete,
        battery_Wh=battery, grid_Wh=grid,
        energy_blocked_pair_evaluations=blocked_pairs,
        energy_blocked_insertions=blocked_insertions,
        dispatches=len(dispatch_ms), over_300ms=misses,
        dispatch_max_ms=max(dispatch_ms,default=None),
        failure_reasons=failure_reasons,
        initial_fleet=settings['initial_fleet'],
        archive_checksums={name:sha(archive/name) for name in
                           (ARCHIVE_FILES + (('energy_feasibility_summary.csv',)
                                             if energy_summary_file.is_file() else ()))},
    )
    return metrics


def rebuild_summary(records):
    fields=['algorithm','trial_index','trial_seed','initial_soc_percent','completed','uncompleted',
            'energy_blocked_pair_evaluations','energy_blocked_insertions','battery_Wh',
            'grid_Wh','dispatches','over_300ms','dispatch_max_ms','canonical_workload_sha256']
    tmp=RESULTS.with_name(RESULTS.name+'.tmp')
    with tmp.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=fields)
        writer.writeheader()
        for r in sorted(records,key=lambda x:(x['algorithm'],x['trial_index'],-x['soc'])):
            if r['status']!='VALIDATED':
                continue
            writer.writerow(dict(algorithm=r['algorithm'],trial_index=r['trial_index'],
                trial_seed=1001+1000*r['trial_index'],initial_soc_percent=round(100*r['soc']),
                **{k:r[k] for k in fields if k not in
                   ('algorithm','trial_index','trial_seed','initial_soc_percent')}))
    os.replace(tmp,RESULTS)


def main():
    global freeze_inventory,release_zip_sha,original_campaign_file,original_campaign_state
    print('===== FIX-24 / STEP 117A: S5 LOW-SOC PILOT (ISOLATED FIX-19) =====',flush=True)
    assert len(SOC_LEVELS)==3 and all(x>.20 for x in SOC_LEVELS)
    freeze_inventory={}
    for line in (FREEZE/'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
        digest,name=line.split('  ',1)
        assert name not in freeze_inventory
        freeze_inventory[name]=digest
    assert len(freeze_inventory)>0
    release_zip_sha=sha(RELEASE)
    original_campaign_file=ROOT/'step108_full640_unified_campaign'/'campaign_640_progress.json'
    original_campaign_state=sha(original_campaign_file) if original_campaign_file.is_file() else None
    check_protected()
    freeze_rec=json.loads((FREEZE/'freeze_record.json').read_text())
    assert freeze_rec['freeze_status']=='FROZEN_CANDIDATE' and freeze_rec['experiment_count']==640
    for name,digest in freeze_rec['source_tree'].items():
        assert sha(SOURCE/name)==digest
    env=json.loads((FREEZE/'environment.json').read_text())
    assert sys.version==env['python'], 'Python version differs from frozen environment'
    assert platform.platform()==env['platform'], 'OS/platform differs from frozen environment'
    for name,version in env['packages'].items():
        assert metadata.version(name)==version, f'Package drift: {name}'
    with ZipFile(RELEASE) as z:
        hashes=json.loads(z.read('release_file_sha256.json'))
        assert set(z.namelist())==set(hashes)|{'release_file_sha256.json'}
        for name,digest in hashes.items():
            assert sha_bytes(z.read(name))==digest, f'Evidence release internal hash changed: {name}'
        reference={}
        for row in rows_bytes(z.read('paired_workload_hashes_80.csv')):
            s,t=row['scenario'],int(row['trial_index'])
            k=(s,t)
            assert k not in reference
            reference[k]=row['canonical_workload_sha256']
        assert len(reference)==80 and all(('S4',t) in reference for t in TRIAL_INDICES)
        release_summary=json.loads(z.read('step115_release_summary.json'))
        assert release_summary['accepted_source_sha256']==SOURCE_SHA
        assert release_summary['original_runs']==640 and release_summary['fix21_promoted'] is False
    print('PASS: Frozen source, complete inventory, environment and Step115 evidence',flush=True)
    print('S5 is an experimental LABEL. FIX-19 command uses --scenario S4 with low-SOC override.',flush=True)
    print('IMPORTANT: SOC22% is severe diagnostic; NOT a realistic operating SOC recommendation.',flush=True)

    cases=[dict(algorithm=a,trial_index=t,soc=soc) for a in ALGORITHMS
           for t in TRIAL_INDICES for soc in SOC_LEVELS]
    assert len(cases)==12 and len({key(x) for x in cases})==12
    protocol=dict(
        protocol_id='FIX24_STEP117A_S5_LOW_SOC_PILOT_V1',
        stage='EXPLORATORY_SEVERITY_SWEEP_NOT_CONFIRMATORY',
        accepted_source_sha256=SOURCE_SHA,
        frozen_manifest_sha256=PROTECTED[FREEZE/'campaign_640_manifest.csv'],
        evidence_release_sha256=release_zip_sha,
        experiment_label='S5', source_scenario='S4', vehicles=12,requests=100,
        algorithms=list(ALGORITHMS),trial_indices=list(TRIAL_INDICES),
        trial_seeds=[1001,2001],initial_soc_levels=list(SOC_LEVELS),
        primary_test_soc=0.25,reference_soc=0.30,severe_diagnostic_soc=0.22,
        reserve_soc=0.20, capacity_Wh=9000,
        constant_settings=dict(alpha_energy=1,beta_time=1,soc_cost_weight=0,
                               cost_mode='normalized',energy_reference_Wh=100,
                               time_reference_s=600,charger_dc_power_W=1500,
                               hvac='off',depot_layout='three',depot_ids=['P02','P08','P16'],
                               dispatch_window_s=30,dispatch_urgent_slack_s=60,
                               max_sim_s=14400),
        primary_diagnostic='Energy-blocked otherwise feasible vehicle-request pairs and insertions',
        secondary_diagnostics=['completed requests','battery reserve','grid electricity','energy consumption',
                               'charging','300ms misses'],
        interpretation_limits=[
            'The pilot has two seeds and two algorithms; it cannot justify statistical superiority.',
            'An energy-blocked insertion/pair counts a modeled feasibility rejection, NOT a separate unserved request.',
            'Low SOC may trigger additional charging: inspect service, grid, and battery jointly.',
            'An observed nonzero blocked count does NOT prove that the battery reserve caused all uncompleted requests.',
            'The original S1-S4 640 experiments remain unchanged.',
            'Modeled battery parameters are NOT calibrated to a physical buggy.',
            'Observed 300 ms behavior is not a hard real-time guarantee.',
            'Do not expand to 160 S5 runs before evaluating this pilot.',
        ], cases=cases)
    WORK.mkdir(parents=True,exist_ok=True)
    if PROTOCOL.exists():
        assert json.loads(PROTOCOL.read_text())==protocol,'STOP: study protocol changed; do not overwrite'
    else:
        assert not any(WORK.iterdir()),'STOP: unrecognized content in pilot directory'
        save_json(PROTOCOL,protocol)
    if PROGRESS.exists():
        progress=json.loads(PROGRESS.read_text())
        assert progress['protocol_sha256']==sha(PROTOCOL) and progress['planned']==12
    else:
        assert {p.name for p in WORK.iterdir()}=={PROTOCOL.name}, 'Unexpected files before new pilot'
        progress=dict(step='117A',fix='FIX-24',protocol_sha256=sha(PROTOCOL),planned=12,
                      created_utc=utc(),records=[])
        save_json(PROGRESS,progress)
    already={}
    for r in progress['records']:
        k=key(r)
        assert k not in already and k in {key(x) for x in cases}
        assert r['status']=='VALIDATED','STOP: interrupted/failed case requires manual investigation; no overwrites'
        archive=Path(r['archive']).resolve()
        assert archive.is_relative_to(WORK) and archive.is_dir()
        for name,digest in r['archive_checksums'].items():
            assert sha(archive/name)==digest, 'STOP: archived pilot run hash changed'
        assert r['canonical_workload_sha256']==reference[('S4',int(r['trial_index']))]
        already[k]=r
    print(f'Previously validated: {len(already)}/12',flush=True)

    runenv=os.environ.copy()
    runenv.update(PYTHONDONTWRITEBYTECODE='1',PYTHONHASHSEED='0',
                  V2_DEPOT_LAYOUT='three',V2_THREE_DEPOT_IDS='P02,P08,P16')
    for spec in cases:
        k=key(spec)
        if k in already:
            continue
        a,t,soc=k
        label=f'S5proxy_S4_{a}_seed{1001+t*1000}_soc{round(soc*100)}'
        wd=WORK/label
        assert not wd.exists(),f'STOP: unexpected preexisting workdir {wd}'
        print(f'\n===== PILOT {len(progress["records"])+1}/12: {label} =====',flush=True)
        entry=dict(**spec,status='STARTED',workdir=str(wd),started_utc=utc())
        progress['records'].append(entry)
        save_json(PROGRESS,progress)
        try:
            shutil.copytree(SOURCE, wd)
            for name in ('app.py','utp_graph.graphml','hotspot_locations.csv'):
                assert sha(wd/name)==sha(SOURCE/name)
            cmd=[sys.executable,'app.py','--headless','--scenario','S4',
                 '--algorithm',a,'--trial-index',str(t),'--max-sim-s','14400',
                 '--flush-every','200','--depot-layout','three',
                 '--dispatch-window-s','30','--dispatch-urgent-slack-s','60',
                 '--initial-soc-range',str(soc),str(soc),
                 '--alpha-energy','1','--beta-time','1','--soc-cost-weight','0',
                 '--cost-mode','normalized','--energy-reference-wh','100',
                 '--time-reference-s','600']
            proc=subprocess.run(cmd,cwd=wd,env=runenv,check=False,
                                capture_output=True,text=True,timeout=1200)
            (wd/'pilot_stdout.txt').write_text(proc.stdout,encoding='utf-8')
            (wd/'pilot_stderr.txt').write_text(proc.stderr,encoding='utf-8')
            assert proc.returncode==0, f'Simulation exit {proc.returncode}: {proc.stderr[-1300:]}'
            status=final_status(proc.stdout)
            assert status['status']=='completed' and status['error'] is None
            archive=Path(status['results_dir']).resolve()
            assert archive.parent==(wd/'results').resolve(), 'Unexpected output directory'
            metrics=verify_archive(archive,spec,reference)
            entry.update(metrics)
            entry['status']='VALIDATED'
            entry['completed_utc']=utc()
            save_json(PROGRESS,progress)
            print(f'PASS: completed={metrics["completed"]}/100 | blocked_pairs={metrics["energy_blocked_pair_evaluations"]} '
                  f'| blocked_insertions={metrics["energy_blocked_insertions"]} '
                  f'| battery={metrics["battery_Wh"]:.2f}Wh | grid={metrics["grid_Wh"]:.2f}Wh '
                  f'| over300ms={metrics["over_300ms"]}',flush=True)
            check_protected()
        except Exception as exc:
            entry['status']='FAILED'
            entry['error']=f'{type(exc).__name__}: {exc}'
            save_json(PROGRESS,progress)
            print(f'STOP: {entry["error"]}',flush=True)
            raise

    assert len(progress['records'])==12 and all(r['status']=='VALIDATED' for r in progress['records'])
    for a in ALGORITHMS:
        for t in TRIAL_INDICES:
            block=[r for r in progress['records'] if r['algorithm']==a and r['trial_index']==t]
            assert len(block)==3 and len({r['canonical_workload_sha256'] for r in block})==1
            # Starting conditions are identical across algorithms at the same SOC/seed.
            for soc in SOC_LEVELS:
                items=[r for r in progress['records'] if r['soc']==soc and r['trial_index']==t]
                assert len(items)==2 and items[0]['initial_fleet']==items[1]['initial_fleet']
    rebuild_summary(progress['records'])
    check_protected()
    print('\n===== STEP 117A S5 LOW-SOC PILOT SUMMARY =====',flush=True)
    for soc in SOC_LEVELS:
        group=[r for r in progress['records'] if r['soc']==soc]
        print(f'SOC={100*soc:.0f}%: total energy-blocked pairs={sum(r["energy_blocked_pair_evaluations"] for r in group)}'
              f' | blocked insertions={sum(r["energy_blocked_insertions"] for r in group)}'
              f' | completed={sum(r["completed"] for r in group)}/{100*len(group)}'
              f' | 300ms misses={sum(r["over_300ms"] for r in group)}',flush=True)
        for r in sorted(group,key=lambda x:(x['algorithm'],x['trial_index'])):
            print(f'  {r["algorithm"]} seed={1001+1000*r["trial_index"]}: '
                  f'blocked pairs={r["energy_blocked_pair_evaluations"]}, '
                  f'completed={r["completed"]}/100, grid={r["grid_Wh"]:.1f}Wh',flush=True)
    print('PASS: All 12 isolated runs validated; all original hashes unchanged',flush=True)
    print(f'Report: {PROGRESS}',flush=True)
    print(f'Summary CSV: {RESULTS}',flush=True)
    print('NEXT: assess whether SOC25% produces genuinely binding energy-feasibility evidence;',flush=True)
    print('      no 160-run campaign is authorized by this pilot alone.',flush=True)
    print('===== STEP 117A COMPLETE =====',flush=True)


if __name__=='__main__':
    main()
