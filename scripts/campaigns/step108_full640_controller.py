from pathlib import Path
from datetime import datetime, timezone
from importlib import metadata
import csv, hashlib, json, math, os, platform, shutil, subprocess, sys

root = Path('research_revision_v2').resolve()
freeze = root / 'step106b_frozen_candidate_20261029T174020Z'
source_dir = freeze / 'simulator'
manifest_path = freeze / 'campaign_640_manifest.csv'
work = root / 'step108_full640_unified_campaign'
report_path = work / 'campaign_640_progress.json'

ALGORITHMS = ('greedy','hungarian','mcmf','auction','random','roundrobin','greedy_minmax','annealing')
SCENARIOS = ('S1','S2','S3','S4')
SIZES = {'S1':(12,30),'S2':(18,50),'S3':(24,100),'S4':(12,100)}
SOURCE_SHA = '7b52bb550414469937da3098f91313b366137d43bd48b8465b840a97bffeb0a0'
MANIFEST_SHA = '33361ce7a27c547cfa0dbb1a1fd37506b75d263d3ccf2334805428d545096189'
expected = {
    root/'fix19_candidate/app.py': SOURCE_SHA,
    root/'fix20_candidate/app.py': '1ae0e683ae4d6f7839841c5bf2123770e8d86482819679a7f533d465d920153d',
    source_dir/'app.py': SOURCE_SHA,
    source_dir/'utp_graph.graphml': 'ebd138a83763f11172e079f40457b371be7eebcd2e3aa018aeda13d9d70114f4',
    source_dir/'hotspot_locations.csv': '6a55a275f2ac18ab383d2efcd67a31250602503a62fa3c5bf2f3f504ae0b979d',
    manifest_path: MANIFEST_SHA,
    freeze/'frozen_bundle.zip': 'f44c29293608268eae77ab5c4fe1a5a890e0671af78cd0ac100ccae8af04683b',
}

FILES = ('run_settings.json','headless_status.json','canonical_requests.csv',
         'request_results.csv','vehicle_results.csv','run_summary.csv',
         'vehicle_soc_snapshots.csv','charging_events.csv',
         'assignment_events.csv','dispatch_invocation_events.csv','timing.csv',
         'backend_source.py','requirements-freeze.txt')

def sha(p):
    assert p.is_file() and not p.is_symlink(), f'Missing/symlink file: {p}'
    h = hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def readcsv(p):
    assert p.is_file(), f'Missing CSV: {p}'
    with p.open(newline='',encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))

def n(row, name):
    v=float(row[name]); assert math.isfinite(v),f'Nonfinite {name}'; return v

def key(r): return (r['scenario'],r['algorithm'],int(r['trial_index']))

def checksum_archive(p):
    return {name:sha(p/name) for name in FILES}

def verify_archive(folder, r):
    s,a,t = key(r)
    seed = 1001 + t*1000
    vehicles, requests = SIZES[s]
    assert folder.is_dir() and not folder.is_symlink()
    status = json.loads((folder/'headless_status.json').read_text())
    settings = json.loads((folder/'run_settings.json').read_text())
    p = settings['parameters']
    assert status['status']=='completed' and status['error'] is None
    assert (status['scenario'],status['algorithm'],int(status['trial_index']),int(status['trial_seed']))==(s,a,t,seed)
    assert status['run_id']==folder.name
    assert (settings['scenario'],settings['algorithm'],int(settings['trial_index']),int(settings['configured_seeds']['TRIAL_SEED']))==(s,a,t,seed)
    assert settings['run_id']==folder.name
    assert settings['backend_source']['sha256']==SOURCE_SHA
    assert sha(folder/'backend_source.py')==SOURCE_SHA
    assert sha(folder/'requirements-freeze.txt')==settings['environment']['package_snapshot']['sha256']
    assert settings['depot_layout']=='three'
    assert settings['three_depot_selection']['parking_ids']==['P02','P08','P16']
    assert len(settings['initial_fleet'])==vehicles
    assert all(abs(n(v,'soc_Wh')-9000)<.01 for v in settings['initial_fleet'])
    for name, val in [('UGV_HVAC_MODE','off'),('PLUGGED_POWER_POLICY','stack_on')]:
        assert p[name]==val, f'Parameter mismatch: {name}'
    for name,val in [('BATTERY_CAPACITY_WH',9000),('BATTERY_RESERVE_FRACTION',.2),
                     ('CHARGER_DC_POWER_W',1500),('ALPHA_ENERGY',1),('BETA_TIME',1),
                     ('SOC_COST_WEIGHT',0),('DISPATCH_WINDOW_S',30),('DISPATCH_URGENT_SLACK_S',60),
                     ('COST_ENERGY_REFERENCE_WH',100),('COST_TIME_REFERENCE_S',600)]:
        assert abs(n(p,name)-val)<1e-8, f'Parameter mismatch: {name}'
    assert p['ASSIGNMENT_COST_MODE']=='normalized'
    canonical=readcsv(folder/'canonical_requests.csv')
    req=readcsv(folder/'request_results.csv')
    veh=readcsv(folder/'vehicle_results.csv')
    soc=readcsv(folder/'vehicle_soc_snapshots.csv')
    charge=readcsv(folder/'charging_events.csv')
    dispatch=readcsv(folder/'dispatch_invocation_events.csv')
    timing=readcsv(folder/'timing.csv')
    assignments=readcsv(folder/'assignment_events.csv')
    summary=readcsv(folder/'run_summary.csv')
    assert len(canonical)==len(req)==requests
    assert len(veh)==vehicles and len(summary)==1 and soc
    assert len({x['request_id'] for x in canonical})==requests
    assert {x['request_id'] for x in canonical}=={x['request_id'] for x in req}
    assert len({x['vehicle_id'] for x in veh})==vehicles
    assert sha(folder/'canonical_requests.csv')==settings['canonical_workload']['sha256']
    completed=0; failures={}
    for q in req:
        assert (q['scenario'],q['algorithm'],int(n(q,'trial_seed')),int(n(q,'trial_index')))==(s,a,seed,t)
        flag=int(n(q,'request_completed'))
        assert flag in (0,1)
        if flag:
            assert q['completion_status']=='completed' and q['pickup_time_s'] and q['dropoff_time_s'] and not q['failure_reason']
            assert n(q,'pickup_time_s')>=n(q,'created_time_s')
            assert n(q,'dropoff_time_s')>=n(q,'pickup_time_s')
            completed+=1
        else:
            assert q['completion_status']=='uncompleted' and q['failure_reason']
            failures[q['failure_reason']]=failures.get(q['failure_reason'],0)+1
    sm=summary[0]
    assert (int(n(sm,'spawned_requests')),int(n(sm,'completed_requests')),int(n(sm,'uncompleted_requests')))==(requests,completed,requests-completed)
    init={}
    for q in soc:
        vid=q['vehicle_id']; start=n(q,'initial_SOC_Wh'); actual=n(q,'SOC_Wh')
        assert abs(start-9000)<.01
        assert abs(actual-(start+n(q,'energy_charged_Wh')-n(q,'energy_consumed_Wh')))<.01
        assert 1799.999<=actual<=9000.001
        if vid in init: assert abs(init[vid]-start)<.01
        init[vid]=start
    assert len(init)==vehicles
    for q in veh:
        vid=q['vehicle_id']; assert vid in init
        assert (q['scenario'],q['algorithm'],int(n(q,'trial_seed')),int(n(q,'trial_index')))==(s,a,seed,t)
        assert abs(init[vid]+n(q,'energy_charged_Wh')-n(q,'energy_consumed_Wh')-n(q,'final_SOC_Wh'))<.01
        assert n(q,'minimum_SOC_Wh')>=1799.999
    stored=sum(n(q,'energy_charged_Wh') for q in veh)
    charged=sum(n(q,'energy_charged_Wh') for q in charge)
    grid=sum(n(q,'grid_energy_kWh')*1000 for q in charge)
    parked=sum(n(q,'parked_load_energy_Wh') for q in charge)
    expected_grid=(parked+stored/n(p,'BATTERY_CHARGE_EFFICIENCY'))/n(p,'CHARGER_EFFICIENCY')
    assert abs(stored-charged)<.05
    assert abs(grid-expected_grid)<.05
    battery=sum(n(q,'energy_consumed_Wh') for q in veh)
    assert abs(battery-n(sm,'total_energy_consumed_Wh'))<.05
    assert abs(grid-n(sm,'total_grid_import_Wh'))<.05
    assert all(q['algorithm']==a and int(n(q,'trial_seed'))==seed for q in assignments)
    times=[n(q,'complete_dispatch_ms') for q in dispatch]
    gaps=[n(q,'matching_cardinality_gap') for q in timing]
    assert all(x>=0 for x in times+gaps)
    misses=sum(x>300 for x in times)
    assert int(n(sm,'dispatch_count'))==len(times)
    assert int(n(sm,'dispatch_budget_miss_count'))==misses
    return {'requests':requests,'vehicles':vehicles,'completed':completed,
            'uncompleted':requests-completed,'failure_reasons':failures,
            'battery_Wh':battery,'grid_Wh':grid,
            'dispatch_count':len(times),'dispatch_over_300ms':misses,
            'dispatch_max_ms':max(times,default=None),
            'matching_gap_rounds':sum(x>0 for x in gaps),
            'canonical_sha256':sha(folder/'canonical_requests.csv'),
            'archive_checksums':checksum_archive(folder)}

def utc(): return datetime.now(timezone.utc).isoformat()

def save():
    tmp=report_path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(progress,indent=2,allow_nan=False),encoding='utf-8')
    os.replace(tmp,report_path)

def protected():
    for file,digest in expected.items(): assert sha(file)==digest,f'PROTECTED CHANGED: {file}'
    for filename,digest in checksum_freeze.items(): assert sha(freeze/filename)==digest,f'FROZEN CHANGED: {filename}'

print('===== FIX-19 / STEP 108: ONE UNIFIED 640-EXPERIMENT CAMPAIGN =====',flush=True)
for file,digest in expected.items(): assert sha(file)==digest, f'STOP: SHA256 mismatch: {file}'
record=json.loads((freeze/'freeze_record.json').read_text())
assert record['freeze_status']=='FROZEN_CANDIDATE' and record['experiment_count']==640
for name,digest in record['source_tree'].items(): assert sha(source_dir/name)==digest
checksum_freeze={}
for line in (freeze/'SHA256SUMS.txt').read_text().splitlines():
    digest,name=line.split('  ',1); checksum_freeze[name]=digest
protected()
env_snapshot=json.loads((freeze/'environment.json').read_text())
assert sys.version==env_snapshot['python']
assert platform.platform()==env_snapshot['platform']
for name,version in env_snapshot['packages'].items():
    assert metadata.version(name)==version, f'ENVIRONMENT DRIFT: {name}'
manifest=readcsv(manifest_path)
assert len(manifest)==640
assert len({key(x) for x in manifest})==640
manifest.sort(key=lambda x:(int(x['trial_index']),SCENARIOS.index(x['scenario']),ALGORITHMS.index(x['algorithm'])))
assert {key(x) for x in manifest}=={(s,a,t) for t in range(20) for s in SCENARIOS for a in ALGORITHMS}
for x in manifest:
    s,a,t=key(x); assert int(x['trial_seed'])==1001+t*1000
    assert (int(x['vehicles']),int(x['requests']))==SIZES[s]
    assert n(x,'alpha_energy')==1 and n(x,'beta_time')==1 and n(x,'soc_cost_weight')==0
print('PASS: Frozen source, environment, manifest and 640 unique experiments',flush=True)

# The first 32 already completed, validated runs are reused, never rerun.
old_reports=[
    (root/'step107_campaign_batch01_20261029T183458Z/batch01_report.json',8),
    (root/'step108a_campaign_seed1001_20261029T184243Z/step108a_report.json',24),
]
prior={}
for path,count in old_reports:
    old=json.loads(path.read_text())
    assert old['attempted']==old['validated']==count and not old['issues']
    for r in old['results']:
        k=key(r); assert k not in prior and int(r['trial_index'])==0 and r['status']=='VALIDATED'
        archive=Path(r['archive']).resolve(); assert archive.is_relative_to(root)
        prior[k]=archive
assert set(prior)=={('S1',a,0) for a in ALGORITHMS}|{(s,a,0) for s in ('S2','S3','S4') for a in ALGORITHMS}

# Do not start a duplicate campaign if the previously proposed 108B batch already began.
legacy = root / 'step108b_campaign_remaining_seeds_2001_20001' / 'step108b_progress.json'
if legacy.is_file():
    existing_legacy = json.loads(legacy.read_text())
    if existing_legacy.get('records'):
        raise RuntimeError('STOP: Step 108B already contains attempted runs. Reconcile those archives first; do not duplicate the campaign.')

work.mkdir(exist_ok=True)
if report_path.exists():
    progress=json.loads(report_path.read_text())
    assert progress['source_sha256']==SOURCE_SHA and progress['manifest_sha256']==MANIFEST_SHA and progress['planned']==640
else:
    assert not any(work.iterdir()), f'STOP: Unregistered files in {work}'
    progress={'campaign':'FIX19_640','source_sha256':SOURCE_SHA,'manifest_sha256':MANIFEST_SHA,
              'planned':640,'created_utc':utc(),'records':[]}
    save()
tracked={}
for r in progress['records']:
    k=key(r); assert k not in tracked
    tracked[k]=r
canonical={}
for r in progress['records']:
    if r['status']!='VALIDATED':
        raise RuntimeError(f'STOP: Unfinished/failed case {key(r)}. Examine it before restart; no overwrite performed.')
    archive=Path(r['archive']).resolve()
    if int(r['trial_index'])==0:
        assert archive==prior[key(r)]
    else:
        assert archive.is_relative_to(work)
    for name,digest in r['archive_checksums'].items():
        assert sha(archive/name)==digest, f'STOP: Archived file changed: {archive/name}'
    pairing=(r['scenario'],int(r['trial_index']))
    if pairing in canonical: assert canonical[pairing]==r['canonical_sha256']
    canonical[pairing]=r['canonical_sha256']

# Import original 32 trials once, with independent read-only verification.
for manifest_row in manifest:
    k=key(manifest_row)
    if k not in prior or k in tracked: continue
    archive=prior[k]
    measured=verify_archive(archive,manifest_row)
    pairing=(k[0],k[2]); assert pairing not in canonical or canonical[pairing]==measured['canonical_sha256']
    canonical[pairing]=measured['canonical_sha256']
    rec={'scenario':k[0],'algorithm':k[1],'trial_index':k[2],
         'trial_seed':1001+k[2]*1000,'archive':str(archive),
         'origin':'prevalidated_step107_or_108a','status':'VALIDATED',**measured}
    progress['records'].append(rec); tracked[k]=rec; save()
assert len(tracked)>=32
print('PASS: 32 existing experiments independently verified and included',flush=True)
print('Current accepted:',len(tracked),'/640; Remaining:',640-len(tracked),flush=True)
print('Unified report:',report_path,flush=True)

run_env=os.environ.copy()
run_env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONHASHSEED='0',V2_DEPOT_LAYOUT='three',V2_THREE_DEPOT_IDS='P02,P08,P16')
error=None
try:
    for m in manifest:
        s,a,t=key(m); k=(s,a,t)
        if k in tracked: continue
        assert t>0
        case_dir=work/f'{s}_{a}_trial{t:02d}'
        if case_dir.exists():
            raise RuntimeError(f'STOP: Run directory exists without a validated checkpoint: {case_dir}')
        entry={'scenario':s,'algorithm':a,'trial_index':t,'trial_seed':1001+t*1000,
               'origin':'step108_unified_execution','status':'STARTED','workdir':str(case_dir),'started_utc':utc()}
        progress['records'].append(entry); save()
        print(f'\n===== RUN {len(tracked)+1}/640: {s} | {a} | seed {1001+t*1000} =====',flush=True)
        try:
            shutil.copytree(source_dir,case_dir)
            assert sha(case_dir/'app.py')==SOURCE_SHA
            cmd=[sys.executable,'app.py','--headless','--scenario',s,'--algorithm',a,
                 '--trial-index',str(t),'--max-sim-s','14400','--flush-every','200',
                 '--depot-layout','three','--dispatch-window-s','30',
                 '--dispatch-urgent-slack-s','60','--initial-soc-range','1.0','1.0',
                 '--alpha-energy','1','--beta-time','1','--soc-cost-weight','0',
                 '--cost-mode','normalized','--energy-reference-wh','100','--time-reference-s','600']
            with (case_dir/'campaign_stdout.log').open('w') as out, (case_dir/'campaign_stderr.log').open('w') as err:
                process=subprocess.run(cmd,cwd=case_dir,env=run_env,stdout=out,stderr=err,timeout=900,check=False)
            assert process.returncode==0, f'Exit code {process.returncode}; inspect campaign_stderr.log'
            archives=[p for p in (case_dir/'results').iterdir() if p.is_dir() and not p.is_symlink()]
            assert len(archives)==1, f'Expected one archived result folder, got {len(archives)}'
            archive=archives[0].resolve()
            assert archive.parent==(case_dir/'results').resolve()
            measured=verify_archive(archive,m)
            pairing=(s,t)
            if pairing in canonical:
                assert canonical[pairing]==measured['canonical_sha256'], 'Canonical workload mismatch across paired algorithms'
            else: canonical[pairing]=measured['canonical_sha256']
            entry.update(measured)
            entry.update(status='VALIDATED',archive=str(archive),completed_utc=utc())
            tracked[k]=entry
            save()
            print(f'PASS: {measured["completed"]}/{measured["requests"]} completed; '
                  f'battery={measured["battery_Wh"]:.3f} Wh; '
                  f'dispatch >300ms={measured["dispatch_over_300ms"]}; '
                  f'campaign={len(tracked)}/640',flush=True)
        except BaseException as ex:
            entry.update(status='INTERRUPTED' if isinstance(ex,KeyboardInterrupt) else 'FAILED',
                         error=f'{type(ex).__name__}: {ex}',ended_utc=utc())
            save()
            error=entry['error']
            print('STOP:',error,flush=True)
            break
finally:
    print('\n===== FINAL PROTECTED SOURCE CHECK =====',flush=True)
    protected()
    print('PASS: FIX-19, FIX-20, frozen bundle and manifest UNCHANGED')
    print('Progress report:',report_path)
    print('Validated:',len(tracked),'/640',flush=True)

if error:
    print('CAMPAIGN STOPPED; preserve the partial run for diagnosis. Do not blindly rerun.')
elif len(tracked)==640:
    print('PASS: ALL 640 EXPERIMENTS VALIDATED IN ONE UNIFIED CAMPAIGN.')
else:
    print('Campaign incomplete; check the progress report.')
print('===== STEP 108 COMPLETE =====')
