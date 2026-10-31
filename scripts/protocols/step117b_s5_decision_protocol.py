#!/usr/bin/env python3
"""FIX-24 / STEP 117B: read-only pilot decision gate + locked S5 analysis protocol.

Run from the utp-uber-app root in the original frozen Python environment.
Inspects 117A results, original frozen inputs and Step115 release. Does NOT
run simulations or modify any existing archives/sources. Writes only to a new
117B work directory. S5 is a label; the frozen simulator runs S4 + SOC override.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import platform
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

BASE = Path('research_revision_v2').resolve()
FREEZE = BASE / 'step106b_frozen_candidate_20261029T174020Z'
SIM = FREEZE / 'simulator'
RELEASE = BASE / 'step115_reproducibility_release/step115_compact_evidence_release.zip'
PILOT_DIR = BASE / 'step117a_s5_battery_stress_pilot'
DEST = BASE / 'step117b_s5_decision_and_protocol'
SOURCE_SHA = '7b52bb550414469937da3098f91313b366137d43bd48b8465b840a97bffeb0a0'
MANIFEST_SHA = '33361ce7a27c547cfa0dbb1a1fd37506b75d263d3ccf2334805428d545096189'
ALGORITHMS = ('greedy', 'hungarian', 'mcmf', 'auction', 'random', 'roundrobin', 'greedy_minmax', 'annealing')
PAIRED_SEEDS = tuple(1001+1000*i for i in range(20))
SOC_LEVELS = (.30, .25, .22)
PROTECTED = {
    BASE/'fix19_candidate/app.py': SOURCE_SHA,
    BASE/'fix20_candidate/app.py': '1ae0e683ae4d6f7839841c5bf2123770e8d86482819679a7f533d465d920153d',
    BASE/'step113a_fix21_candidate_pilot/fix21_source_copy/app.py': '2e466def64d70a710fa43a77d9d4c1d3229a6c8cff5a080777e9309f5c303e15',
    BASE/'step113c_fix21_geometry_candidate_pilot/fix21_geometry_source_copy/app.py': '518eeb94dd2e794d9d6b8460de8e94332cb6ef8a1da75d3142b262cd43d62975',
    SIM/'app.py': SOURCE_SHA,
    SIM/'utp_graph.graphml': 'ebd138a83763f11172e079f40457b371be7eebcd2e3aa018aeda13d9d70114f4',
    SIM/'hotspot_locations.csv': '6a55a275f2ac18ab383d2efcd67a31250602503a62fa3c5bf2f3f504ae0b979d',
    FREEZE/'campaign_640_manifest.csv': MANIFEST_SHA,
    FREEZE/'frozen_bundle.zip': 'f44c29293608268eae77ab5c4fe1a5a890e0671af78cd0ac100ccae8af04683b',
}


def sha(path: Path) -> str:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise AssertionError(f'Missing/symlink file: {path}')
    h = hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda: f.read(1024*1024), b''):
            h.update(part)
    return h.hexdigest()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def csv_rows(path: Path):
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def csv_bytes(data: bytes):
    return list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))


def number(value):
    n = float(value)
    assert math.isfinite(n), f'Invalid numeric value: {value}'
    return n


def equal_float(a, b, tol=1e-4):
    return math.isclose(number(a), number(b), rel_tol=0, abs_tol=tol)


def read_json(p):
    return json.loads(p.read_text(encoding='utf-8'))


def atomic_write(p, data: bytes):
    if p.exists():
        assert p.read_bytes() == data, f'STOP: existing output differs: {p}'
        return
    temp = p.with_name(p.name+'.tmp')
    assert not temp.exists(), f'STOP: leftover pending write: {temp}'
    with temp.open('xb') as f:
        f.write(data)
    os.replace(temp, p)


def json_data(obj):
    return (json.dumps(obj, indent=2, sort_keys=True, allow_nan=False)+'\n').encode('utf-8')


def prepare_manifest(source_rows):
    s4 = [r for r in source_rows if r['scenario']=='S4']
    assert len(s4) == 160
    assert {r['algorithm'] for r in s4} == set(ALGORITHMS)
    ordered = sorted(s4, key=lambda r:(ALGORITHMS.index(r['algorithm']), int(r['trial_index'])))
    assert len({(r['algorithm'],r['trial_index']) for r in ordered}) == 160
    output = []
    for r in ordered:
        assert int(r['trial_seed']) == 1001+1000*int(r['trial_index'])
        assert int(r['trial_seed']) in PAIRED_SEEDS
        assert int(r['vehicles']) == 12 and int(r['requests']) == 100
        assert r['depot_layout'] == 'three' and r['depot_ids'] == 'P02,P08,P16'
        assert equal_float(r['alpha_energy'],1) and equal_float(r['beta_time'],1)
        assert equal_float(r['soc_cost_weight'],0)
        assert equal_float(r['initial_soc_low'],1) and equal_float(r['initial_soc_high'],1)
        assert equal_float(r['charger_dc_power_w'],1500)
        assert r['hvac_mode']=='off'
        output.append(dict(
            study_id='FIX24_S5_25PCT_SOC', supplement_label='S5', source_scenario='S4',
            algorithm=r['algorithm'], trial_index=r['trial_index'],trial_seed=r['trial_seed'],
            vehicles=r['vehicles'],requests=r['requests'],
            initial_soc_low='0.25',initial_soc_high='0.25',
            battery_reserve_fraction='0.20', nominal_capacity_Wh='9000',
            depot_layout=r['depot_layout'],depot_ids=r['depot_ids'],
            alpha_energy=r['alpha_energy'],beta_time=r['beta_time'],
            soc_cost_weight=r['soc_cost_weight'],charger_dc_power_w=r['charger_dc_power_w'],
            hvac_mode=r['hvac_mode'], frozen_source_sha256=SOURCE_SHA,
        ))
    buff=io.StringIO(newline='')
    writer=csv.DictWriter(buff, fieldnames=list(output[0]))
    writer.writeheader()
    writer.writerows(output)
    return buff.getvalue().encode('utf-8'),output


def main():
    print('===== FIX-24 / STEP 117B: S5 PILOT DECISION & PREREGISTRATION =====',flush=True)
    for file, digest in PROTECTED.items():
        assert sha(file)==digest, f'STOP: protected source/input hash mismatch: {file}'
    inventory={}
    for line in (FREEZE/'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
        digest,name=line.split('  ',1)
        assert name not in inventory
        inventory[name]=digest
    assert inventory
    for name,digest in inventory.items():
        assert sha(FREEZE/name)==digest, f'STOP: frozen inventory mismatch {name}'
    frozen_record=read_json(FREEZE/'freeze_record.json')
    assert frozen_record['experiment_count']==640
    for name,digest in frozen_record['source_tree'].items():
        assert sha(SIM/name)==digest
    # Ensure the old campaign progress is untouched by this gate.
    campaign_file=BASE/'step108_full640_unified_campaign/campaign_640_progress.json'
    campaign_before=sha(campaign_file)
    release_before=sha(RELEASE)
    pilot_progress_path=PILOT_DIR/'step117a_progress.json'
    pilot_protocol_path=PILOT_DIR/'step117a_protocol.json'
    pilot_summary_path=PILOT_DIR/'step117a_summary.csv'
    progress_before=sha(pilot_progress_path)
    summary_before=sha(pilot_summary_path)
    pilot_protocol_before=sha(pilot_protocol_path)
    pilot_protocol=read_json(pilot_protocol_path)
    progress=read_json(pilot_progress_path)
    assert pilot_protocol['protocol_id']=='FIX24_STEP117A_S5_LOW_SOC_PILOT_V1'
    assert pilot_protocol['accepted_source_sha256']==SOURCE_SHA
    assert pilot_protocol['evidence_release_sha256']==release_before
    assert pilot_protocol['primary_test_soc']==.25
    assert progress['protocol_sha256']==pilot_protocol_before
    assert progress['planned']==12 and len(progress['records'])==12
    print('PASS: FIX-19/FIX-20/FIX-21 hashes, frozen bundle, 640-run manifest',flush=True)

    with ZipFile(RELEASE) as z:
        hashed=json.loads(z.read('release_file_sha256.json'))
        assert set(z.namelist())==set(hashed)|{'release_file_sha256.json'}
        for name,digest in hashed.items():
            assert sha_bytes(z.read(name))==digest, f'STOP: release hash mismatch: {name}'
        release_meta=json.loads(z.read('step115_release_summary.json'))
        assert release_meta['original_runs']==640 and release_meta['accepted_source_sha256']==SOURCE_SHA
        assert release_meta['fix21_promoted'] is False
        original_manifest=csv_bytes(z.read('freeze/campaign_640_manifest.csv'))
        assert sha_bytes(z.read('freeze/campaign_640_manifest.csv'))==MANIFEST_SHA
        workload_rows=csv_bytes(z.read('paired_workload_hashes_80.csv'))
        ref_workload={(r['scenario'],int(r['trial_index'])):r['canonical_workload_sha256'] for r in workload_rows}
        assert len(ref_workload)==80
        manifest_bytes,manifest_entries=prepare_manifest(original_manifest)
    print('PASS: Step115 compact evidence release hashes and 160 matched S4 rows',flush=True)

    expected={(a,t,s) for a in ('greedy','hungarian') for t in (0,1) for s in SOC_LEVELS}
    seen={}
    p_metrics=[]
    for r in progress['records']:
        k=(r['algorithm'],int(r['trial_index']),float(r['soc']))
        assert k in expected and k not in seen
        assert r['status']=='VALIDATED'
        archive=Path(r['archive']).resolve()
        assert archive.is_dir() and archive.is_relative_to(PILOT_DIR.resolve())
        assert r['canonical_workload_sha256']==ref_workload[('S4',k[1])]
        for filename,digest in r['archive_checksums'].items():
            assert sha(archive/filename)==digest, f'STOP: pilot archive edited: {archive}/{filename}'
        st=read_json(archive/'headless_status.json')
        rs=read_json(archive/'run_settings.json')
        assert st['status']=='completed' and st['error'] is None
        assert st['algorithm']==k[0] and int(st['trial_seed'])==1001+1000*k[1]
        assert st['scenario']=='S4' and rs['scenario']=='S4'
        assert rs['scenario_config']['initial_soc_range']==[k[2],k[2]]
        assert rs['backend_source']['sha256']==SOURCE_SHA
        assert sha(archive/'canonical_requests.csv')==ref_workload[('S4',k[1])]
        result_rows=csv_rows(archive/'request_results.csv')
        assert len(result_rows)==100
        completed=sum(int(number(x['request_completed'])) for x in result_rows)
        assert completed==int(r['completed']) and 100-completed==int(r['uncompleted'])
        sm=csv_rows(archive/'run_summary.csv')
        assert len(sm)==1 and int(number(sm[0]['completed_requests']))==completed
        assert equal_float(sm[0]['total_energy_consumed_Wh'],r['battery_Wh'],tol=.05)
        assert equal_float(sm[0]['total_grid_import_Wh'],r['grid_Wh'],tol=.05)
        assert int(st.get('energy_blocked_pair_evaluations') or 0)==int(r['energy_blocked_pair_evaluations'])
        assert int(st.get('energy_blocked_insertions') or 0)==int(r['energy_blocked_insertions'])
        energy=csv_rows(archive/'vehicle_results.csv')
        assert len(energy)==12 and all(number(v['minimum_SOC_Wh'])>=1799.999 for v in energy)
        dispatch=csv_rows(archive/'dispatch_invocation_events.csv')
        assert int(r['over_300ms'])==sum(number(x['complete_dispatch_ms'])>300 for x in dispatch)
        seen[k]=r
        p_metrics.append(dict(algorithm=k[0],seed=1001+1000*k[1],soc_pct=round(k[2]*100),
                              completed=completed, blocked_pairs=int(r['energy_blocked_pair_evaluations']),
                              blocked_insertions=int(r['energy_blocked_insertions']),
                              battery_wh=number(r['battery_Wh']),grid_wh=number(r['grid_Wh']),
                              missed_dispatches=int(r['over_300ms'])))
    assert set(seen)==expected
    for t in (0,1):
        for soc in SOC_LEVELS:
            pair=[seen[(a,t,soc)] for a in ('greedy','hungarian')]
            assert pair[0]['canonical_workload_sha256']==pair[1]['canonical_workload_sha256']
            assert pair[0]['initial_fleet']==pair[1]['initial_fleet']
    summary_original={
        (x['algorithm'],int(x['trial_index']),int(x['initial_soc_percent'])):x
        for x in csv_rows(pilot_summary_path)
    }
    assert len(summary_original)==12
    for r in p_metrics:
        q=summary_original[(r['algorithm'],(r['seed']-1001)//1000,r['soc_pct'])]
        assert int(q['completed'])==r['completed']
        assert int(q['energy_blocked_pair_evaluations'])==r['blocked_pairs']
        assert int(q['energy_blocked_insertions'])==r['blocked_insertions']
        assert equal_float(q['battery_Wh'],r['battery_wh'],tol=.05)
        assert equal_float(q['grid_Wh'],r['grid_wh'],tol=.05)
    print('PASS: All 12 pilot archives, original rows, battery reserves and source identities',flush=True)

    grouped=defaultdict(list)
    for r in p_metrics:
        grouped[r['soc_pct']].append(r)
    for soc in (30,25,22):
        assert len(grouped[soc])==4
    assert all(r['blocked_pairs']==0 for r in grouped[30])
    assert all(r['blocked_pairs']>0 and r['blocked_insertions']>0 for r in grouped[25])
    assert sum(r['completed'] for r in grouped[25])==393
    assert sum(r['completed'] for r in grouped[30])==400
    assert sum(r['completed'] for r in grouped[22])==270
    # This is a prospective follow-up chosen after reviewing the exploratory pilot,
    # not a pre-registered hypothesis that preceded the pilot.
    protocol={
        'study_name':'FIX-24 supplemental S5 near-reserve battery-stress experiment',
        'study_design_status':'PROSPECTIVE FULL-CAMPAIGN PROTOCOL CREATED AFTER EXPLORATORY STEP117A',
        'original_main_campaign':'FIX-19 S1–S4 640 runs remain accepted, frozen and unchanged',
        'source_code_sha256':SOURCE_SHA,
        'frozen_manifest_sha256':MANIFEST_SHA,
        'step115_release_sha256':release_before,
        'step117a_pilot_progress_sha256':progress_before,
        'study_label':'S5',
        'actual_cli_scenario':'S4',
        'workload':'S4 canonical 12 UGV / 100 requests, unchanged road network, depot and arrival demand',
        'starting_soc_fraction':.25,
        'battery_reserve_fraction':.20,
        'nominal_battery_wh':9000,
        'nominal_wh_above_reserve_at_start':450,
        'charging_power_w':1500,
        'algorithms':list(ALGORITHMS),
        'paired_seeds':list(PAIRED_SEEDS),
        'trials_per_algorithm':20,
        'campaign_planned_runs':160,
        'alpha_energy':1,
        'beta_time':1,
        'soc_cost_weight':0,
        'dispatch_budget_ms':300,
        'completion_gate':'Every run must archive full settings and matching canonical workload hash, pass model energy/SOC ledger and reserve validation, and retain original frozen source and environment hashes',
        'primary_operational_outcome':'completed requests / 100 per seed, with request noncompletion reasons',
        'battery_feasibility_outcome':'energy-blocked candidate evaluations and rejected insertions; these are repeated algorithm evaluations, not independent refused requests',
        'additional_reported_outcomes':[
            'charging events and grid electricity Wh', 'battery energy withdrawn Wh',
            'battery-energy per completed request, with fulfillment reported alongside',
            'pickup waiting times where defined, with missing/noncompleted request handling described',
            'fleet service and battery reserve violations',
            'dispatch complete processing ms and occurrences >300 ms',
        ],
        'analysis_plan':[
            'Summarize all eight algorithms over the 20 matched seeds, with denominator 2,000 requested trips per algorithm.',
            'Give paired per-seed completion count differences against Hungarian for the seven comparisons.',
            'For inferential claims use a prespecified two-sided paired randomization/sign-flip test where appropriate and Holm correction across seven fulfillment comparisons; clearly label assumptions and ties.',
            'Report effect sizes and paired uncertainty intervals; do not infer algorithm superiority from two-seed exploratory pilot.',
            'Battery-block counts are descriptive diagnostics, not independent Bernoulli observations.',
            'Consider service, energy and grid input jointly; lower total energy with more unserved trips is not automatically better.',
            'Compare with existing full-SOC S4 only as a contextual matched-seed sensitivity; full 30% SOC 8-algorithm control is NOT part of these 160 runs.',
            'No cross-scenario causal claim without controlling for the SOC setting and demand.',
            'No physical battery accuracy or hard real-time guarantee claim.',
        ],
        'workload_identity':'Use S4 seed-specific canonical workload SHA256 from accepted Step115 paired_workload_hashes_80.csv',
        'pilot_22_percent':'Severe diagnostic only, not proposed normal operating condition.',
        'pilot_30_percent':'Exploratory comparator for 2 algorithms × 2 seeds only, not the full confirmatory control.',
        'pilot_25_percent':'Condition selected after viewing pilot for the supplementary campaign.',
        'safeguard':'This step ONLY preregisters; starting the 160 simulations requires a separate Step117C controller.',
    }
    assert len(manifest_entries)==160
    summary={}
    for soc in (30,25,22):
        group=grouped[soc]
        summary[str(soc)]={
            'total_runs':4,
            'completed':sum(r['completed'] for r in group),
            'requested':400,
            'energy_blocked_candidate_pair_evaluations':sum(r['blocked_pairs'] for r in group),
            'energy_blocked_insertions':sum(r['blocked_insertions'] for r in group),
            'deadline_misses':sum(r['missed_dispatches'] for r in group),
        }
    report={
        'decision':'PASS: 25% SOC is demonstrably battery binding in all four tested runs; authorize design of separately controlled 160-run S5 campaign',
        'inference_limit':'Exploratory 2-seed pilot, 2 algorithms; no general algorithm ranking or statistical significance claim',
        'pilot_results':summary,
        'pilot_archive_sha256':progress_before,
        's5_manifest_sha256':sha_bytes(manifest_bytes),
        'study_protocol_sha256':sha_bytes(json_data(protocol)),
        'read_only_reconciled':True,
    }
    DEST.mkdir(parents=True,exist_ok=True)
    atomic_write(DEST/'step117b_s5_160_manifest.csv', manifest_bytes)
    atomic_write(DEST/'step117b_s5_analysis_protocol.json',json_data(protocol))
    atomic_write(DEST/'step117b_pilot_decision.json',json_data(report))
    for path, digest in PROTECTED.items():
        assert sha(path)==digest, f'STOP: protected identity drift: {path}'
    for name, digest in inventory.items():
        assert sha(FREEZE/name)==digest
    assert sha(RELEASE)==release_before and sha(campaign_file)==campaign_before
    assert sha(pilot_progress_path)==progress_before and sha(pilot_summary_path)==summary_before
    assert sha(pilot_protocol_path)==pilot_protocol_before
    print('\n===== STEP 117B PILOT DECISION & LOCKED S5 PROTOCOL =====',flush=True)
    for soc in (30,25,22):
        s=summary[str(soc)]
        print(f'SOC {soc}%: blocked_pairs={s["energy_blocked_candidate_pair_evaluations"]}; '
              f'blocked_insertions={s["energy_blocked_insertions"]}; '
              f'completed={s["completed"]}/{s["requested"]}; '
              f'>300ms={s["deadline_misses"]}',flush=True)
    print('DECISION: SOC 25% SELECTED as experimental S5 stress condition',flush=True)
    print('DESIGN: 8 algorithms × 20 matched seeds = 160 planned NEW S5 runs',flush=True)
    print('PRIMARY: completion count per seed; energy-blocked candidate evaluations are DIAGNOSTIC',flush=True)
    print('STATUS: 160-run S5 campaign NOT STARTED by Step117B',flush=True)
    print(f'Protocol: {DEST/"step117b_s5_analysis_protocol.json"}',flush=True)
    print(f'Locked manifest: {DEST/"step117b_s5_160_manifest.csv"}',flush=True)
    print('PASS: Original FIX-19/FIX-20/FIX-21 sources, pilot and all original archives unchanged',flush=True)
    print('===== STEP 117B COMPLETE =====',flush=True)


if __name__=='__main__':
    main()
