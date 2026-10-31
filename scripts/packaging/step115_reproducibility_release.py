#!/usr/bin/env python3
"""FIX-19 / STEP 115: read-only integrity inventory and compact release package.

Never runs simulations, imports simulator, overwrites experiments, or changes source.
The ZIP includes reproducibility inputs/metadata, results tables and indexed
checksums; it deliberately does NOT embed all 640 complete run archives.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sys
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('research_revision_v2').resolve()
FREEZE = ROOT / 'step106b_frozen_candidate_20261029T174020Z'
SOURCE_SHA = '7b52bb550414469937da3098f91313b366137d43bd48b8465b840a97bffeb0a0'
MANIFEST_SHA = '33361ce7a27c547cfa0dbb1a1fd37506b75d263d3ccf2334805428d545096189'
CAMPAIGN = ROOT / 'step108_full640_unified_campaign/campaign_640_progress.json'
OUTPUT = ROOT / 'step115_reproducibility_release'
SCENARIOS = ('S1','S2','S3','S4')
ALGOS = ('greedy','hungarian','mcmf','auction','random','roundrobin','greedy_minmax','annealing')
ALL_KEYS = {(s,a,t) for t in range(20) for s in SCENARIOS for a in ALGOS}

PROTECTED = {
    ROOT / 'fix19_candidate/app.py': SOURCE_SHA,
    ROOT / 'fix20_candidate/app.py': '1ae0e683ae4d6f7839841c5bf2123770e8d86482819679a7f533d465d920153d',
    FREEZE / 'simulator/app.py': SOURCE_SHA,
    FREEZE / 'simulator/utp_graph.graphml': 'ebd138a83763f11172e079f40457b371be7eebcd2e3aa018aeda13d9d70114f4',
    FREEZE / 'simulator/hotspot_locations.csv': '6a55a275f2ac18ab383d2efcd67a31250602503a62fa3c5bf2f3f504ae0b979d',
    FREEZE / 'campaign_640_manifest.csv': MANIFEST_SHA,
    FREEZE / 'frozen_bundle.zip': 'f44c29293608268eae77ab5c4fe1a5a890e0671af78cd0ac100ccae8af04683b',
    ROOT / 'step113a_fix21_candidate_pilot/fix21_source_copy/app.py': '2e466def64d70a710fa43a77d9d4c1d3229a6c8cff5a080777e9309f5c303e15',
    ROOT / 'step113c_fix21_geometry_candidate_pilot/fix21_geometry_source_copy/app.py': '518eeb94dd2e794d9d6b8460de8e94332cb6ef8a1da75d3142b262cd43d62975',
}

EVIDENCE = {
    'campaign/campaign_640_progress.json': CAMPAIGN,
    'analysis/run_level_640.csv': ROOT / 'step109a_independent_analysis_20261029T213146Z/run_level_640.csv',
    'analysis/scenario_algorithm_summary.csv': ROOT / 'step109a_independent_analysis_20261029T213146Z/scenario_algorithm_summary.csv',
    'analysis/paired_vs_hungarian.csv': ROOT / 'step109a_independent_analysis_20261029T213146Z/paired_vs_hungarian.csv',
    'analysis/step109a_audit_report.json': ROOT / 'step109a_independent_analysis_20261029T213146Z/step109a_audit_report.json',
    'ablation/holdout_paired.csv': ROOT / 'step110c_holdout_alpha_ablation/paired_alpha1_minus_alpha0_holdout.csv',
    'ablation/step110d_paired_inference.csv': ROOT / 'step110d_holdout_inference/step110d_paired_inference.csv',
    'ablation/Step110D_Holdout_Interpretation.md': ROOT / 'step110d_holdout_inference/Step110D_Holdout_Interpretation.md',
    'physics/step111b_exploratory_sensitivity.csv': ROOT / 'step111b_physical_parameter_sensitivity_pilot/step111b_exploratory_sensitivity.csv',
    'latency/step112a_latency_audit.json': ROOT / 'step112a_dispatch_latency_forensics_20261030T080212Z/step112a_latency_audit.json',
    'latency/step112c_repeatability_results.csv': ROOT / 'step112c_unprofiled_repeatability/step112c_repeatability_results.csv',
    'latency/step113d_decision.json': ROOT / 'step113d_fix21_decision_gate/step113d_decision.json',
    'latency/step114_timing_qualification.json': ROOT / 'step114_timing_claim_qualification/step114_timing_qualification.json',
    'latency/step114_scenario_timing.csv': ROOT / 'step114_timing_claim_qualification/step114_scenario_timing.csv',
    'latency/Step114_Manuscript_Timing_Language.md': ROOT / 'step114_timing_claim_qualification/Step114_Manuscript_Timing_Language.md',
    'freeze/freeze_record.json': FREEZE / 'freeze_record.json',
    'freeze/environment.json': FREEZE / 'environment.json',
    'freeze/SHA256SUMS.txt': FREEZE / 'SHA256SUMS.txt',
    'freeze/campaign_640_manifest.csv': FREEZE / 'campaign_640_manifest.csv',
}

def require(ok, message):
    if not ok:
        raise RuntimeError('STOP: ' + str(message))

def sha(file):
    file = Path(file)
    require(file.is_file() and not file.is_symlink(), f'missing or symlink file: {file}')
    h=hashlib.sha256()
    with file.open('rb') as f:
        for part in iter(lambda: f.read(1024*1024),b''):
            h.update(part)
    return h.hexdigest()

def readcsv(file):
    with Path(file).open('r',newline='',encoding='utf-8-sig') as f:
        reader=csv.DictReader(f)
        require(bool(reader.fieldnames),f'no CSV header: {file}')
        return list(reader)

def key(r):
    return r['scenario'],r['algorithm'],int(r['trial_index'])

def protected():
    for file,digest in PROTECTED.items():
        require(sha(file)==digest,f'protected source mismatch {file}')
    record=json.loads((FREEZE/'freeze_record.json').read_text(encoding='utf-8'))
    require(record['freeze_status']=='FROZEN_CANDIDATE' and record['experiment_count']==640,'frozen metadata changed')
    for name,digest in record['source_tree'].items():
        file=FREEZE/'simulator'/name
        require(file.resolve().is_relative_to(FREEZE.resolve()),'unsafe frozen source path')
        require(sha(file)==digest,f'frozen source tree mismatch {name}')
    for line in (FREEZE/'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
        digest,name=line.split('  ',1)
        file=FREEZE/name
        require(file.resolve().is_relative_to(FREEZE.resolve()),'unsafe frozen checksum path')
        require(sha(file)==digest,f'frozen archive checksum mismatch {name}')

print('===== FIX-19 / STEP 115: PUBLICATION EVIDENCE FREEZE =====',flush=True)
require(not OUTPUT.exists(),f'output already exists: {OUTPUT}; preserve it, do not overwrite')
protected()
print('PASS: Original FIX-19, FIX-20, FIX-21 and frozen package hashes',flush=True)
for logical,path in EVIDENCE.items():
    require(path.is_file() and not path.is_symlink(),f'publication evidence missing: {logical}: {path}')

records=json.loads(CAMPAIGN.read_text(encoding='utf-8'))
require(records.get('campaign')=='FIX19_640' and records.get('planned')==640,'incorrect campaign identity')
require(records['source_sha256']==SOURCE_SHA and records['manifest_sha256']==MANIFEST_SHA,'campaign source or manifest differs')
require(len(records['records'])==640,'campaign must contain 640 records')
by_key={}
for record in records['records']:
    k=key(record)
    require(k in ALL_KEYS and k not in by_key,f'missing/duplicate/unexpected run: {k}')
    require(record['status']=='VALIDATED' and int(record['trial_seed'])==1001+1000*k[2],f'not validated or wrong seed: {k}')
    by_key[k]=record
require(set(by_key)==ALL_KEYS,'incomplete experiment matrix')

manifest=readcsv(FREEZE/'campaign_640_manifest.csv')
require(len(manifest)==640 and {key(r) for r in manifest}==ALL_KEYS,'manifest/campaign keys disagree')

index=[]
ledger=[]
paired={}
mandatory={
    'run_settings.json','canonical_requests.csv','run_summary.csv',
    'request_results.csv','vehicle_results.csv','assignment_events.csv',
    'dispatch_invocation_events.csv','charging_events.csv',
    'backend_source.py','requirements-freeze.txt',
}
verified_files=0
for i,k in enumerate(sorted(ALL_KEYS,key=lambda x:(x[2],SCENARIOS.index(x[0]),ALGOS.index(x[1]))),1):
    s,a,t=k
    r=by_key[k]
    archive=Path(r['archive']).resolve()
    require(archive.is_dir() and archive.is_relative_to(ROOT),f'unsafe/missing archive: {k}')
    checks=r['archive_checksums']
    require(mandatory.issubset(checks),f'critical archive checksums missing: {k}')
    for name,digest in sorted(checks.items()):
        require(Path(name).name==name and '/' not in name and '\\' not in name,f'unsafe filename {name}')
        file=archive/name
        require(sha(file)==digest,f'archived output changed: {k}/{name}')
        ledger.append({'scenario':s,'algorithm':a,'trial_index':t,
                       'relative_archive':str(archive.relative_to(ROOT)),
                       'filename':name,'sha256':digest,'bytes':file.stat().st_size})
        verified_files+=1
    require(checks['backend_source.py']==SOURCE_SHA,f'run snapshot differs from frozen FIX-19: {k}')
    settings=json.loads((archive/'run_settings.json').read_text(encoding='utf-8'))
    require(settings['backend_source']['sha256']==SOURCE_SHA,'incorrect run backend identity')
    require(settings['scenario']==s and settings['algorithm']==a and int(settings['trial_index'])==t,'archive settings mismatch')
    require(int(settings['configured_seeds']['TRIAL_SEED'])==1001+1000*t,'archive seed mismatch')
    digest=checks['canonical_requests.csv']
    require(digest==r['canonical_sha256'],f'canonical workload mismatch: {k}')
    paired_key=(s,t)
    require(paired_key not in paired or paired[paired_key]==digest,f'paired workload differs: {s} seed index {t}')
    paired[paired_key]=digest
    index.append({'scenario':s,'algorithm':a,'trial_index':t,'trial_seed':1001+1000*t,
                  'archive_relative_to_research_revision_v2':str(archive.relative_to(ROOT)),
                  'canonical_workload_sha256':digest,
                  'completed_requests':r.get('completed',''),
                  'battery_Wh':r.get('battery_Wh',r.get('battery_consumed_Wh','')),
                  'dispatch_count':r.get('dispatch_count',''),
                  'dispatch_over_300ms':r.get('dispatch_over_300ms',''),
                  'number_of_checksums':len(checks)})
    if i%80==0:
        print(f'Verified complete run-file checksum inventories: {i}/640',flush=True)
require(len(paired)==80,'wrong paired workload count')
print(f'PASS: 640 unique runs, 80 paired workloads, {verified_files:,} archived file checksums',flush=True)

# Independent fixed-count timing qualification remains the canonical timing source.
timing=json.loads(EVIDENCE['latency/step114_timing_qualification.json'].read_text(encoding='utf-8'))
require(timing['runs']==640 and timing['dispatches']==25512 and timing['deadline_violations']==77,'Step 114 verification differs')
require(timing['accepted_main_campaign_source']=='FIX-19' and timing['fix21_geometry_promoted'] is False,'wrong promotion decision')
infer=readcsv(EVIDENCE['ablation/step110d_paired_inference.csv'])
require(len(infer)==4,'expected four supplementary holdout hypotheses')
physics=readcsv(EVIDENCE['physics/step111b_exploratory_sensitivity.csv'])
require(len(physics)==24,'expected 24 exploratory physics comparisons')
run_metrics=readcsv(EVIDENCE['analysis/run_level_640.csv'])
require(len(run_metrics)==640,'wrong independent analysis row count')

# All checks have passed before any publication files are created.
OUTPUT.mkdir(parents=False)
for logical,path in EVIDENCE.items():
    destination=OUTPUT/logical
    destination.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(path,destination)
    require(sha(destination)==sha(path),f'copy verification failed: {logical}')

with (OUTPUT/'archive_index_640.csv').open('x',newline='',encoding='utf-8') as f:
    writer=csv.DictWriter(f,fieldnames=list(index[0]));writer.writeheader();writer.writerows(index)
with (OUTPUT/'archive_file_checksums.csv').open('x',newline='',encoding='utf-8') as f:
    writer=csv.DictWriter(f,fieldnames=list(ledger[0]));writer.writeheader();writer.writerows(ledger)

with (OUTPUT/'paired_workload_hashes_80.csv').open('x',newline='',encoding='utf-8') as f:
    writer=csv.writer(f);writer.writerow(['scenario','trial_index','trial_seed','canonical_workload_sha256'])
    for (scenario,t),digest in sorted(paired.items()):
        writer.writerow([scenario,t,1001+1000*t,digest])

full_bundle=FREEZE/'frozen_bundle.zip'
if full_bundle.stat().st_size <= 150*1024*1024:
    shutil.copy2(full_bundle,OUTPUT/'freeze'/'frozen_bundle.zip')
    require(sha(OUTPUT/'freeze'/'frozen_bundle.zip')==sha(full_bundle),'frozen bundle copy changed')
    bundle_mode='included'
else:
    bundle_mode='omitted due to 150 MiB size cap; retrieve original frozen bundle using hash in inventory'

readme='''# FIX-19 / Step 115: Publication Reproducibility Evidence

## Status
- Accepted benchmark: frozen **FIX-19** source; 640 unique experiment runs, 4 scenarios × 8 algorithms × 20 paired seeds.
- Frozen experiment archives are **NOT** rewritten by this packaging step.
- The two FIX-21 candidates remain **experimental and unpromoted**; they are NOT part of the original results.
- The original 300 ms budget was exceeded 77 times in 25,512 measured dispatch invocations (0.302%). This is empirical soft-deadline performance, not a hard real-time guarantee.
- Energy-weighted ablation provides conditional simulated energy-savings evidence; it is not physical battery-model validation.
- Sensitivity testing documents uncertain physical parameters but does not establish empirical vehicle calibration.

## Contents
- `freeze/`: frozen source package (if under size cap), manifest, hashes and environment metadata.
- `campaign/`: original 640-run progress report with archive locations and checksums.
- `analysis/`: independent run-level comparisons and summary statistics.
- `ablation/`: 18-seed holdout comparisons and corrected exploratory inference.
- `physics/`: illustrative parameter-sensitivity summaries.
- `latency/`: timing qualification and original FIX-21 rejection evidence.
- `archive_index_640.csv`: one row per accepted experiment; paths are relative to `research_revision_v2`.
- `archive_file_checksums.csv`: SHA256 for each validated run file.
- `paired_workload_hashes_80.csv`: exact workload identities across algorithms.

## Reconstructing the complete research archive
This ZIP is a **compact evidence/index package** and does not contain all 640 complete raw simulation directories.
Keep the original run archives at their recorded paths, or copy them independently, preserving the directory names and hashes in `archive_file_checksums.csv`. Complete raw-event reproducibility requires those archives in addition to the compact ZIP.

## Scientific limits
The 640-run benchmark is simulation-based, at nominal 100% starting SOC and energy-weighted cost. The supplementary alpha=0/1 comparison tests the energy-cost contribution in selected S3/S4 Hungarian and Greedy cases. Actual vehicle power measurements were not supplied. Dispatch timing does not include sensing, communications, actuator execution, or system-wide worst-case latency.

## Integrity
Check the `release_file_sha256.json` document after extraction and the recorded SHA256 values before using or publishing a dataset. Do not replace the original accepted source with a candidate optimization.
'''
(OUTPUT/'README.md').write_text(readme,encoding='utf-8')
summary={
    'step':'115','status':'REPRODUCIBILITY_INDEX_VERIFIED',
    'created_utc':datetime.now(timezone.utc).isoformat(),
    'accepted_source_sha256':SOURCE_SHA,'accepted_manifest_sha256':MANIFEST_SHA,
    'original_runs':640,'scenario_seed_pairs':80,'archive_files_sha256_verified':verified_files,
    'dispatches':25512,'over_300ms':77,
    'fix21_promoted':False,'physical_energy_calibrated':False,
    'raw_640_archives_embedded':False,'frozen_bundle':bundle_mode,
}
(OUTPUT/'step115_release_summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
release_hashes={str(p.relative_to(OUTPUT)):sha(p) for p in sorted(OUTPUT.rglob('*')) if p.is_file()}
(OUTPUT/'release_file_sha256.json').write_text(json.dumps(release_hashes,indent=2)+'\n',encoding='utf-8')

zip_path=OUTPUT/'step115_compact_evidence_release.zip'
with zipfile.ZipFile(zip_path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for file in sorted(OUTPUT.rglob('*')):
        if file.is_file() and file!=zip_path:
            z.write(file,file.relative_to(OUTPUT))
with zipfile.ZipFile(zip_path,'r') as z:
    require(z.testzip() is None,'generated release ZIP has CRC errors')
    require(set(release_hashes).issubset(z.namelist()),'generated release ZIP lacks evidence')
    for name,digest in release_hashes.items():
        require(hashlib.sha256(z.read(name)).hexdigest()==digest,f'ZIP member SHA mismatch: {name}')
protected()
print('\n===== STEP 115 FINAL REPRODUCIBILITY INVENTORY =====')
print('Accepted benchmark: FIX-19 / 640 unique runs / 80 paired workloads')
print('Archive files independently hash-checked:',f'{verified_files:,}')
print('Dispatches / measured >300 ms:', '25512 / 77')
print('FIX-21 promotion: REJECTED')
print('Real-vehicle energy model: NOT physically calibrated')
print('Frozen bundle:',bundle_mode)
print('Full raw experiment archives: indexed and verified, NOT included in compact ZIP')
print('Original FIX-19, FIX-20, FIX-21, campaign and supplementary archives: UNCHANGED')
print('Release directory:',OUTPUT)
print('Upload this compact ZIP for final manuscript work:',zip_path)
print('===== STEP 115 COMPLETE =====')
