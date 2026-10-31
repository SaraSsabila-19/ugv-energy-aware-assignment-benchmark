#!/usr/bin/env python3
"""FIX-19 / STEP 114: read-only qualification of the original dispatch timing evidence.

Rebuild the original 640-run dispatch statistics, reconcile the independent
Step 112A audit, and document why this is measured soft-deadline performance,
not a hard real-time guarantee. Does not import the simulator or start runs.
Original experimental data and frozen code are never written to.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import csv
import hashlib
import json
import math
import statistics
import zipfile

ROOT = Path('research_revision_v2').resolve()
FREEZE = ROOT / 'step106b_frozen_candidate_20261029T174020Z'
SOURCE_SHA = '7b52bb550414469937da3098f91313b366137d43bd48b8465b840a97bffeb0a0'
MANIFEST_SHA = '33361ce7a27c547cfa0dbb1a1fd37506b75d263d3ccf2334805428d545096189'
PROTECTED = {
    ROOT / 'fix19_candidate/app.py': SOURCE_SHA,
    ROOT / 'fix20_candidate/app.py': '1ae0e683ae4d6f7839841c5bf2123770e8d86482819679a7f533d465d920153d',
    ROOT / 'step113a_fix21_candidate_pilot/fix21_source_copy/app.py': '2e466def64d70a710fa43a77d9d4c1d3229a6c8cff5a080777e9309f5c303e15',
    ROOT / 'step113c_fix21_geometry_candidate_pilot/fix21_geometry_source_copy/app.py': '518eeb94dd2e794d9d6b8460de8e94332cb6ef8a1da75d3142b262cd43d62975',
    FREEZE / 'simulator/app.py': SOURCE_SHA,
    FREEZE / 'simulator/utp_graph.graphml': 'ebd138a83763f11172e079f40457b371be7eebcd2e3aa018aeda13d9d70114f4',
    FREEZE / 'simulator/hotspot_locations.csv': '6a55a275f2ac18ab383d2efcd67a31250602503a62fa3c5bf2f3f504ae0b979d',
    FREEZE / 'campaign_640_manifest.csv': MANIFEST_SHA,
    FREEZE / 'frozen_bundle.zip': 'f44c29293608268eae77ab5c4fe1a5a890e0671af78cd0ac100ccae8af04683b',
}
CAMPAIGN = ROOT / 'step108_full640_unified_campaign/campaign_640_progress.json'
FORENSICS = ROOT / 'step112a_dispatch_latency_forensics_20261030T080212Z'
AUDIT = FORENSICS / 'step112a_latency_audit.json'
REPEATS = ROOT / 'step112c_unprofiled_repeatability/step112c_progress.json'
DECISION = ROOT / 'step113d_fix21_decision_gate/step113d_decision.json'
OUTPUT = ROOT / 'step114_timing_claim_qualification'
SCENARIOS = ('S1', 'S2', 'S3', 'S4')
ALGORITHMS = ('greedy','hungarian','mcmf','auction','random','roundrobin','greedy_minmax','annealing')
PHASES = ('matrix_ms','solver_ms','cardinality_diagnostic_ms','activation_ms','other_dispatch_ms')
EXPECTED_KEYS = {(s,a,t) for s in SCENARIOS for a in ALGORITHMS for t in range(20)}


def check(ok, reason):
    if not ok:
        raise RuntimeError('STOP: '+str(reason))


def sha(path):
    path = Path(path)
    check(path.is_file() and not path.is_symlink(), f'missing or linked file {path}')
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024), b''):
            h.update(b)
    return h.hexdigest()


def readcsv(path):
    check(path.is_file() and not path.is_symlink(),f'missing CSV {path}')
    with path.open(newline='',encoding='utf-8-sig') as f:
        r=csv.DictReader(f)
        check(bool(r.fieldnames),f'missing header {path}')
        return list(r)


def number(row, col):
    v=float(row[col])
    check(math.isfinite(v),f'non-finite {col}')
    return v


def intval(row,col):
    v=number(row,col)
    check(v.is_integer(),f'non-integer {col}={v}')
    return int(v)


def percentile(values,p):
    a=sorted(values)
    check(bool(a),'no values for percentile')
    x=(len(a)-1)*p
    i=int(x)
    return a[i] if i==len(a)-1 else a[i]*(1-(x-i))+a[i+1]*(x-i)


def key(r):
    return r['scenario'],r['algorithm'],int(r['trial_index'])


def source_integrity():
    for f,d in PROTECTED.items():
        check(sha(f)==d,f'protected hash mismatch: {f}')
    record=json.loads((FREEZE/'freeze_record.json').read_text(encoding='utf-8'))
    check(record['freeze_status']=='FROZEN_CANDIDATE' and record['experiment_count']==640,'freeze metadata')
    for name,d in record['source_tree'].items():
        check(sha(FREEZE/'simulator'/name)==d,f'frozen source tree changed: {name}')
    for line in (FREEZE/'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
        d,name=line.split('  ',1)
        check(sha(FREEZE/name)==d,f'frozen checksum mismatch: {name}')


def writecsv(path, data):
    check(bool(data),'empty table')
    with path.open('x',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(data[0]))
        w.writeheader()
        w.writerows(data)


print('===== FIX-19 / STEP 114: FINAL TIMING CLAIM QUALIFICATION =====',flush=True)
source_integrity()
print('PASS: Protected source, graph, manifest and full frozen checksum inventory',flush=True)

progress=json.loads(CAMPAIGN.read_text(encoding='utf-8'))
check(progress.get('campaign')=='FIX19_640' and progress.get('planned')==640,'wrong campaign identity')
check(progress['source_sha256']==SOURCE_SHA and progress['manifest_sha256']==MANIFEST_SHA,'wrong source/manifest')
records=progress['records']
check(len(records)==640,'campaign record count is not 640')
by_key={}
for r in records:
    k=key(r)
    check(k in EXPECTED_KEYS and k not in by_key,f'unexpected/duplicate {k}')
    check(r['status']=='VALIDATED' and intval(r,'trial_seed')==1001+1000*k[2],f'unvalidated {k}')
    by_key[k]=r
check(set(by_key)==EXPECTED_KEYS,'not all 640 scenario/algorithm/seed combinations present')

by_s=defaultdict(list)
all_rows=[]
paired={}
for i,k in enumerate(sorted(EXPECTED_KEYS,key=lambda z:(z[2],SCENARIOS.index(z[0]),ALGORITHMS.index(z[1]))),1):
    s,a,t=k
    r=by_key[k]
    arc=Path(r['archive']).resolve()
    check(arc.is_dir() and arc.is_relative_to(ROOT),f'unsafe archived directory {k}')
    checksums=r['archive_checksums']
    for filename in ('run_settings.json','canonical_requests.csv','run_summary.csv','dispatch_invocation_events.csv'):
        check(filename in checksums and sha(arc/filename)==checksums[filename],f'archived file/hash changed: {k}/{filename}')
    settings=json.loads((arc/'run_settings.json').read_text(encoding='utf-8'))
    check(settings['backend_source']['sha256']==SOURCE_SHA,f'source identity changed: {k}')
    check(settings['scenario']==s and settings['algorithm']==a and int(settings['configured_seeds']['TRIAL_SEED'])==1001+1000*t,f'archive identity changed: {k}')
    paired_key=(s,t)
    digest=sha(arc/'canonical_requests.csv')
    check(digest==r['canonical_sha256'],f'workload digest mismatch: {k}')
    check(paired_key not in paired or paired[paired_key]==digest,f'unpaired scenario/seed workload {k}')
    paired[paired_key]=digest
    summary=readcsv(arc/'run_summary.csv')
    check(len(summary)==1,f'multiple or absent summary rows: {k}')
    inv=readcsv(arc/'dispatch_invocation_events.csv')
    check(intval(summary[0],'dispatch_count')==len(inv),f'dispatch count mismatch: {k}')
    missed=0
    for row in inv:
        duration=number(row,'complete_dispatch_ms')
        budget=number(row,'dispatch_budget_ms')
        check(abs(budget-300)<1e-6,f'budget changed from 300 ms: {k}')
        flag=intval(row,'budget_missed')
        check(flag in (0,1) and flag==int(duration>budget),f'miss-flag discrepancy: {k}')
        phases=[number(row,p) for p in PHASES]
        check(abs(math.fsum(phases)-duration)<.005,f'phase-time discrepancy: {k}')
        check(all(v>=-.005 for v in phases),f'negative phase-time: {k}')
        missed+=flag
        item={'scenario':s,'algorithm':a,'trial_index':t,'trial_seed':1001+1000*t,'dispatch_ms':duration,'miss':flag,
              'matrix_ms':phases[0],'solver_ms':phases[1],'cardinality_diagnostic_ms':phases[2],
              'activation_ms':phases[3],'other_dispatch_ms':phases[4]}
        by_s[s].append(item)
        all_rows.append(item)
    check(missed==intval(summary[0],'dispatch_budget_miss_count')==intval(r,'dispatch_over_300ms'),f'run miss-count mismatch: {k}')
    if i%80==0:
        print(f'Revalidated original dispatch records: {i}/640',flush=True)
check(len(paired)==80,'wrong number of paired workloads')
check(len(all_rows)==25512 and sum(r['miss'] for r in all_rows)==77,'historical dispatch count/miss count changed')

original=json.loads(AUDIT.read_text(encoding='utf-8'))
check(original['status']=='READ_ONLY_LATENCY_AUDIT_PASSED' and original['source_sha256']==SOURCE_SHA,'Step 112A audit identity')
check(original['dispatch_invocations']==len(all_rows) and original['over_300ms']==77,'Step 112A totals disagree')
reported={row['scenario']:row for row in original['scenario_summary']}
scenario_rows=[]
for s in SCENARIOS:
    rr=by_s[s]
    durations=[r['dispatch_ms'] for r in rr]
    missed=sum(r['miss'] for r in rr)
    reference=reported[s]
    check(len(rr)==int(reference['dispatch_invocations']) and missed==int(reference['over_300ms']),f'Step 112A scenario counts disagree: {s}')
    p99=percentile(durations,.99)
    check(abs(p99-float(reference['measured_p99_ms']))<.001,f'Step 112A p99 disagrees: {s}')
    scenario_rows.append({'scenario':s,'dispatches':len(rr),'over_300ms':missed,'miss_pct':100*missed/len(rr),
                          'p50_ms':percentile(durations,.5),'p95_ms':percentile(durations,.95),
                          'p99_ms':p99,'maximum_ms':max(durations)})
check(sum(row['dispatches'] for row in scenario_rows)==len(all_rows),'scenario totals incorrect')

missed_rows=[r for r in all_rows if r['miss']==1]
miss_total=math.fsum(r['dispatch_ms'] for r in missed_rows)
miss_matrix_pct=100*math.fsum(r['matrix_ms'] for r in missed_rows)/miss_total
ref_phase=next(p for p in original['phase_summary'] if p['scope']=='MISSED DISPATCHES' and p['phase']=='matrix_ms')
check(abs(miss_matrix_pct-float(ref_phase['share_of_total_dispatch_ms']))<.001,'Step 112A matrix phase attribution disagrees')

repeat=json.loads(REPEATS.read_text(encoding='utf-8'))
check(repeat['step']=='112C' and repeat['planned']==20 and len(repeat['records'])==20,'Step 112C repeatability report missing/incomplete')
check(all(item['status']=='VALIDATED' for item in repeat['records']),'Step 112C has unvalidated runs')
critical=[item for item in repeat['records'] if item['label'].startswith('S3_greedy_minmax_seed10001_repeat')]
check(len(critical)==5,'Step 112C critical workload repeats missing')
check(all(float(item['target_repeat_ms'])>300 for item in critical),'Step 112C repeatability evidence changed')

decision=json.loads(DECISION.read_text(encoding='utf-8'))
check(decision['step']=='113D' and decision['validated_archives']==16,'Step 113D decision evidence missing')
check(decision['promote_to_main_benchmark'] is False and decision['critical_deadline_gate_passed'] is False,'Step 113D decision changed')
print('PASS: All 640 original archive timing records independently reconciled')
print('PASS: Step 112A forensics, Step 112C repeatability and Step 113D rejection agree')

# The only created files are inside an independent output directory.
OUTPUT.mkdir(exist_ok=True)
result_csv=OUTPUT/'step114_scenario_timing.csv'
report_path=OUTPUT/'step114_timing_qualification.json'
manuscript_path=OUTPUT/'Step114_Manuscript_Timing_Language.md'
if any(p.exists() for p in (result_csv,report_path,manuscript_path,OUTPUT/'step114_shareable_timing.zip')):
    raise RuntimeError('STOP: Step 114 output already exists. Preserve it; do not overwrite existing evidence.')

writecsv(result_csv,scenario_rows)
report={
    'step':'114','created_utc':datetime.now(timezone.utc).isoformat(),
    'reviewed_frozen_source_sha256':SOURCE_SHA,'reviewed_manifest_sha256':MANIFEST_SHA,
    'runs':640,'paired_scenario_seed_workloads':80,'dispatches':len(all_rows),
    'recorded_dispatch_budget_ms':300,'deadline_violations':len(missed_rows),
    'measured_violation_pct':100*len(missed_rows)/len(all_rows),
    'measured_total_p99_ms':percentile([r['dispatch_ms'] for r in all_rows],.99),
    'measured_worst_dispatch_ms':max(r['dispatch_ms'] for r in all_rows),
    'matrix_share_of_missed_dispatch_wall_time_pct':miss_matrix_pct,
    'scenarios':scenario_rows,
    'repeatability_critical_S3_greedy_minmax':{
        'repetitions':len(critical),
        'target_above_300ms':sum(float(r['target_repeat_ms'])>300 for r in critical),
        'target_min_ms':min(float(r['target_repeat_ms']) for r in critical),
        'target_max_ms':max(float(r['target_repeat_ms']) for r in critical)},
    'fix21_geometry_promoted':False,
    'accepted_main_campaign_source':'FIX-19',
    'interpretation':'Observed instrumented dispatch processing on tested software and hardware; NOT a hard real-time worst-case guarantee.',
    'excluded_from_measured_scope':['sensing and perception delays','network communication','actuator commands','complete robot control-loop delays','deployment worst-case scheduling','independent physical hardware validation'],
}
report_path.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')

lines=[
    '# FIX-19 / Step 114: Timing qualification for manuscript',
    '',
    '## Publication-ready Results text',
    '',
    f'Across 640 simulation runs (80 scenario–seed workload sets, each evaluated using eight assignment algorithms), '
    f'{len(all_rows):,} instrumented dispatch invocations were measured under a nominal 300 ms dispatch-processing budget. '
    f'A total of {len(missed_rows)} invocations ({100*len(missed_rows)/len(all_rows):.3f}%) exceeded this threshold. '
    f'The maximum recorded dispatch duration was {max(r["dispatch_ms"] for r in all_rows):.2f} ms. '
    f'Among the budget-exceeding invocations, cost-matrix construction accounted for {miss_matrix_pct:.2f}% '
    'of the summed measured dispatch wall-clock duration; solver execution was a comparatively small contributor. '
    'These observations characterize the tested Python-based simulation environment, not a verified worst-case execution-time bound.',
    '',
    '| Scenario | Dispatches | >300 ms | Miss rate | p99 (ms) | Max (ms) |',
    '|---|---:|---:|---:|---:|---:|',
]
for row in scenario_rows:
    lines.append(f'| {row["scenario"]} | {row["dispatches"]:,} | {row["over_300ms"]} | {row["miss_pct"]:.3f}% | {row["p99_ms"]:.2f} | {row["maximum_ms"]:.2f} |')
lines += [
    '', '## Suggested limitations text', '',
    'The 300 ms threshold was not guaranteed: the original benchmark recorded 77 violations. '
    'Selected unprofiled reruns confirmed a repeatable budget overrun for the S3 Greedy Min-Max workload, '
    'while several extreme original outliers did not recur. A separate FIX-21 geometry-cache candidate '
    'preserved deterministic outcomes in selected tests but failed the deadline-margin acceptance gate '
    'and was not promoted. Instrumented dispatch times exclude sensing, communications, actuation, '
    'end-to-end closed-loop control, and deployment-level worst-case scheduling interference. '
    'Accordingly, this work evaluates *real-time-oriented algorithmic dispatch in simulation*, '
    'not certified hard real-time operation.',
    '', '## Reporting boundaries', '',
    '- All percentages are empirical frequencies, not guaranteed failure probabilities.',
    '- Dispatches within the same run are correlated; do not treat all dispatch invocations as independent statistical trials.',
    '- Original outliers and budget violations remain included in the benchmark.',
    '- FIX-21 tests are supplementary, not part of the frozen 640-run comparison.',
    '- Hardware energy-model accuracy was not validated by these timing tests.',
    '- Main source and all original experiment archives were not modified.',
]
manuscript_path.write_text('\n'.join(lines)+'\n',encoding='utf-8')

zip_path=OUTPUT/'step114_shareable_timing.zip'
with zipfile.ZipFile(zip_path,'x',compression=zipfile.ZIP_DEFLATED) as z:
    for p in (result_csv,report_path,manuscript_path):
        z.write(p,p.name)
    for name in ('latency_by_scenario_algorithm.csv','latency_phase_breakdown.csv','latency_top_77_violations.csv'):
        z.write(FORENSICS/name,'step112a_evidence/'+name)
with zipfile.ZipFile(zip_path) as z:
    check(z.testzip() is None,'output ZIP corrupt')
source_integrity()

print('\n===== STEP 114: FINAL MEASURED DISPATCH PERFORMANCE =====')
print(f"{'Scenario':9}{'Dispatches':>11}{'Over 300ms':>12}{'Rate':>10}{'p99 ms':>12}{'Worst ms':>12}")
for r in scenario_rows:
    print(f"{r['scenario']:9}{r['dispatches']:11d}{r['over_300ms']:12d}{r['miss_pct']:9.3f}%{r['p99_ms']:12.2f}{r['maximum_ms']:12.2f}")
print('TOTAL:',len(all_rows),'dispatches;',len(missed_rows),'misses;',f'{100*len(missed_rows)/len(all_rows):.3f}%')
print('Matrix share of missed-dispatch time:',f'{miss_matrix_pct:.2f}%')
print('Critical S3 repeats over 300ms:',len(critical),'/5')
print('FIX-21 promotion: REJECTED; original FIX-19 benchmark retained')
print('CLAIM: Measured soft-deadline performance only; NO hard real-time guarantee')
print('PASS: Protected sources and original 640 archives unchanged')
print('Manuscript language:',manuscript_path)
print('Shareable ZIP:',zip_path)
print('===== STEP 114 COMPLETE =====')
