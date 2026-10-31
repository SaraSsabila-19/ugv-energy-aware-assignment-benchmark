#!/usr/bin/env python3
"""FIX-24 / STEP 117C: isolated, checksum-guarded 160-run S5 campaign.

Run from ~/utp-uber-app with the SAME venv and files used in Steps 117A/B.
Uses frozen FIX-19 app.py with CLI --scenario S4 and --initial-soc-range
0.25 0.25; S5 is only a supplementary STUDY LABEL. Nothing in the accepted
S1–S4 campaign, the frozen source, or Steps 117A/117B is rewritten.

Progress checkpoints after each case. Validated runs may be skipped on restart
only after independently rechecking archived bytes and outcome invariants.
A failed or truly interrupted case is preserved and requires investigation;
it is never silently deleted or overwritten.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
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
RELEASE = ROOT / 'step115_reproducibility_release/step115_compact_evidence_release.zip'
DECISION = ROOT / 'step117b_s5_decision_and_protocol'
MANIFEST = DECISION / 'step117b_s5_160_manifest.csv'
PROTOCOL = DECISION / 'step117b_s5_analysis_protocol.json'
DECISION_REPORT = DECISION / 'step117b_pilot_decision.json'
WORK = ROOT / 'step117c_s5_full160_campaign'
PROGRESS = WORK / 'step117c_progress.json'
SUMMARY = WORK / 'step117c_run_level_160.csv'
SOURCE_SHA = '7b52bb550414469937da3098f91313b366137d43bd48b8465b840a97bffeb0a0'
STEP117A_SCRIPT_SHA = '94520296da755bd701c3e265e47b828763d7ee7f40a54d83cae33295697764bb'
STEP117B_SCRIPT_SHA = 'e16eb0163b21f381bc775f67448735a509292ca9bbd25b26ab26c90d297aca30'
ALGORITHMS = ('greedy', 'hungarian', 'mcmf', 'auction', 'random', 'roundrobin', 'greedy_minmax', 'annealing')
EXPECTED_KEYS = {(a, t) for a in ALGORITHMS for t in range(20)}
STEP117A = Path('step117a_s5_battery_stress_pilot.py').resolve()
STEP117B = Path('step117b_s5_decision_protocol.py').resolve()


def sha(path):
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise AssertionError(f'STOP: file missing or is a symlink: {p}')
    digest = hashlib.sha256()
    with p.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def atomic_json(path, value):
    target = Path(path)
    tmp = target.with_name(target.name + '.tmp')
    assert not tmp.exists(), f'STOP: unexpected partial checkpoint {tmp}'
    with tmp.open('x', encoding='utf-8') as output:
        json.dump(value, output, indent=2, sort_keys=True, allow_nan=False)
        output.write('\n')
    os.replace(tmp, target)


def csv_rows(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as file:
        return list(csv.DictReader(file))


def csv_bytes(data):
    return list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))


def manifest_cases(rows):
    """Validate the full set and all fixed settings BEFORE any experiments."""
    assert len(rows) == 160, 'Locked S5 manifest must have 160 rows'
    keys = set()
    for r in rows:
        assert (r['study_id'], r['supplement_label'], r['source_scenario']) == ('FIX24_S5_25PCT_SOC', 'S5', 'S4')
        a, t, seed = r['algorithm'], int(r['trial_index']), int(r['trial_seed'])
        k = (a, t)
        assert k in EXPECTED_KEYS and k not in keys, 'Missing/duplicate/unexpected algorithm-seed case'
        keys.add(k)
        assert seed == 1001 + 1000 * t
        assert int(r['vehicles']) == 12 and int(r['requests']) == 100
        assert float(r['initial_soc_low']) == float(r['initial_soc_high']) == .25
        assert float(r['battery_reserve_fraction']) == .20
        assert float(r['nominal_capacity_Wh']) == 9000
        assert r['depot_layout'] == 'three' and r['depot_ids'] == 'P02,P08,P16'
        assert float(r['alpha_energy']) == float(r['beta_time']) == 1
        assert float(r['soc_cost_weight']) == 0
        assert float(r['charger_dc_power_w']) == 1500
        assert r['hvac_mode'] == 'off' and r['frozen_source_sha256'] == SOURCE_SHA
    assert keys == EXPECTED_KEYS
    return sorted(rows, key=lambda r: (ALGORITHMS.index(r['algorithm']), int(r['trial_index'])))


def load_checked_module(name, script_path, digest):
    assert sha(script_path) == digest, f'STOP: {script_path.name} differs from validated controller'
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def preflight():
    """Build an immutable view of the study's reference evidence."""
    print('===== FIX-24 / STEP 117C: LOCKED 160-RUN S5 CAMPAIGN =====', flush=True)
    a = load_checked_module('fix24_step117a_validator', STEP117A, STEP117A_SCRIPT_SHA)
    b = load_checked_module('fix24_step117b_protocol_builder', STEP117B, STEP117B_SCRIPT_SHA)

    inventory = {}
    for line in (FREEZE / 'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
        digest, name = line.split('  ', 1)
        assert name not in inventory
        inventory[name] = digest
    assert inventory
    protected = dict(a.PROTECTED)
    protected[STEP117A] = STEP117A_SCRIPT_SHA
    protected[STEP117B] = STEP117B_SCRIPT_SHA
    for path, digest in protected.items():
        assert sha(path) == digest, f'STOP: original or candidate source changed: {path}'
    for rel, digest in inventory.items():
        assert sha(FREEZE / rel) == digest, f'STOP: frozen inventory changed: {rel}'
    record = read_json(FREEZE / 'freeze_record.json')
    assert record['freeze_status'] == 'FROZEN_CANDIDATE' and record['experiment_count'] == 640
    for rel, digest in record['source_tree'].items():
        assert sha(SOURCE / rel) == digest
    env = read_json(FREEZE / 'environment.json')
    assert sys.version == env['python'], 'STOP: Python version differs from frozen environment'
    # FIX-24 / STEP 117C-K1: narrowly attest the only observed environment drift.
    # Never rewrite the original freeze or silently relax software/package checks.
    frozen_platform = env['platform']
    current_platform = platform.platform()
    allowed_frozen_platform = 'Linux-7.0.0-38-generic-x86_64-with-glibc2.43'
    allowed_current_platform = 'Linux-7.0.0-42-generic-x86_64-with-glibc2.43'
    assert frozen_platform == allowed_frozen_platform, (
        f'STOP: unrecognized frozen platform: {frozen_platform}')
    assert current_platform == allowed_current_platform, (
        f'STOP: unapproved current platform: {current_platform}')
    assert env['machine'] == platform.machine() == 'x86_64', (
        'STOP: architecture differs from approved freeze')
    assert platform.libc_ver() == ('glibc', '2.43'), (
        'STOP: glibc differs from approved freeze')
    print('DOCUMENTED KERNEL CHANGE: 7.0.0-38-generic -> 7.0.0-42-generic', flush=True)
    print('PASS: Architecture=x86_64; glibc=2.43; Python exact match', flush=True)
    environment_delta = {
        'frozen_platform': frozen_platform,
        'current_platform': current_platform,
        'frozen_python': env['python'],
        'current_python': sys.version,
        'machine': platform.machine(),
        'glibc': list(platform.libc_ver()),
        'exception_scope': 'Exact documented Linux kernel revision only; not proof of identical runtime timing',
    }
    for name, version in env['packages'].items():
        assert metadata.version(name) == version, f'STOP: package version drift: {name}'

    original_campaign = ROOT / 'step108_full640_unified_campaign/campaign_640_progress.json'
    pilot_dir = ROOT / 'step117a_s5_battery_stress_pilot'
    pinned = {
        original_campaign: sha(original_campaign),
        RELEASE: sha(RELEASE),
        pilot_dir / 'step117a_progress.json': sha(pilot_dir / 'step117a_progress.json'),
        pilot_dir / 'step117a_protocol.json': sha(pilot_dir / 'step117a_protocol.json'),
        pilot_dir / 'step117a_summary.csv': sha(pilot_dir / 'step117a_summary.csv'),
        MANIFEST: sha(MANIFEST),
        PROTOCOL: sha(PROTOCOL),
        DECISION_REPORT: sha(DECISION_REPORT),
    }
    for path, digest in pinned.items():
        assert sha(path) == digest

    with ZipFile(RELEASE) as archive:
        hash_index = json.loads(archive.read('release_file_sha256.json'))
        assert set(archive.namelist()) == set(hash_index) | {'release_file_sha256.json'}
        for name, digest in hash_index.items():
            assert sha_bytes(archive.read(name)) == digest, f'STOP: damaged release member: {name}'
        meta = json.loads(archive.read('step115_release_summary.json'))
        assert meta['original_runs'] == 640 and meta['accepted_source_sha256'] == SOURCE_SHA
        assert meta['fix21_promoted'] is False
        originals = csv_bytes(archive.read('freeze/campaign_640_manifest.csv'))
        regenerated, entries = b.prepare_manifest(originals)
        assert len(entries) == 160
        assert MANIFEST.read_bytes() == regenerated, 'STOP: locked manifest differs from accepted S4 manifest'
        baseline = {}
        for r in csv_bytes(archive.read('paired_workload_hashes_80.csv')):
            key = (r['scenario'], int(r['trial_index']))
            assert key not in baseline
            baseline[key] = r['canonical_workload_sha256']
        assert len(baseline) == 80 and all(('S4', i) in baseline for i in range(20))

    protocol = read_json(PROTOCOL)
    decision = read_json(DECISION_REPORT)
    assert protocol['campaign_planned_runs'] == 160
    assert protocol['starting_soc_fraction'] == .25 and protocol['battery_reserve_fraction'] == .20
    assert protocol['source_code_sha256'] == SOURCE_SHA
    assert protocol['step115_release_sha256'] == pinned[RELEASE]
    assert protocol['step117a_pilot_progress_sha256'] == pinned[pilot_dir / 'step117a_progress.json']
    assert protocol['algorithms'] == list(ALGORITHMS)
    assert protocol['paired_seeds'] == [1001 + 1000 * x for x in range(20)]
    assert protocol['actual_cli_scenario'] == 'S4'
    assert decision['s5_manifest_sha256'] == pinned[MANIFEST]
    assert decision['study_protocol_sha256'] == pinned[PROTOCOL]
    assert decision['read_only_reconciled'] is True
    case_rows = manifest_cases(csv_rows(MANIFEST))

    # The simulator / freeze never need or receive our new results directory.
    print('PASS: 160 locked cases, 20 matched S4 workloads, post-pilot analysis protocol', flush=True)
    print('PASS: Frozen FIX-19 source, FIX-20/FIX-21, Step115, Step117A/B, environment', flush=True)
    print('NOTE: S5 is a supplementary label; executable scenario remains S4 + SOC=0.25.', flush=True)
    return dict(validator=a, protected=protected, inventory=inventory, pinned=pinned,
                reference=baseline, cases=case_rows,
                environment_delta=environment_delta)


def ensure_protected(ctx):
    for path, digest in ctx['protected'].items():
        assert sha(path) == digest, f'STOP: protected code/input modified: {path}'
    for name, digest in ctx['inventory'].items():
        assert sha(FREEZE / name) == digest, f'STOP: frozen inventory modified: {name}'
    for path, digest in ctx['pinned'].items():
        assert sha(path) == digest, f'STOP: prior evidence or protocol modified: {path}'


def ident(r):
    return r['algorithm'], int(r['trial_index'])


def workdir_for(r):
    return WORK / f'S5proxy_S4_{r["algorithm"]}_seed{int(r["trial_seed"])}_soc25'


def validate_case(ctx, spec, archive):
    """Run exact Step117A battery, charge, workload and timing checks."""
    archive = Path(archive).resolve()
    workdir = workdir_for(spec).resolve()
    assert archive.parent == (workdir / 'results').resolve(), f'STOP: result outside isolated workdir: {archive}'
    assert archive.is_dir() and not archive.is_symlink()
    metrics = ctx['validator'].verify_archive(archive,
                 dict(algorithm=spec['algorithm'], trial_index=int(spec['trial_index']), soc=.25),
                 ctx['reference'])
    assert metrics['canonical_workload_sha256'] == ctx['reference'][('S4', int(spec['trial_index']))]
    assert metrics['completed'] + metrics['uncompleted'] == 100
    assert len(metrics['initial_fleet']) == 12
    return metrics


def compare_record_to_metrics(rec, metrics):
    for name, digest in rec['archive_checksums'].items():
        assert sha(Path(rec['archive']) / name) == digest, f'STOP: prior S5 archive changed: {name}'
    for field in ('completed', 'uncompleted', 'energy_blocked_pair_evaluations',
                  'energy_blocked_insertions', 'dispatches', 'over_300ms',
                  'canonical_workload_sha256'):
        assert rec[field] == metrics[field], f'STOP: prior run result changed: {field}'
    for field in ('battery_Wh', 'grid_Wh'):
        assert math.isclose(float(rec[field]), float(metrics[field]), rel_tol=0, abs_tol=.001)
    assert rec['archive_checksums'] == metrics['archive_checksums']


def recover_completed_started(ctx, rec, spec, progress):
    """Only recover an *already completed* but uncheckpointed run; never rerun in place."""
    wd = workdir_for(spec)
    stdout_path = wd / 'pilot_stdout.txt'
    if not stdout_path.is_file():
        raise RuntimeError(f'STOP: interrupted run preserved at {wd}; stdout not final. Do NOT overwrite.')
    payload = ctx['validator'].final_status(stdout_path.read_text(encoding='utf-8'))
    assert payload['status'] == 'completed' and payload['error'] is None
    archive = Path(payload['results_dir']).resolve()
    result = validate_case(ctx, spec, archive)
    rec.update(result)
    rec.update(status='VALIDATED', completed_utc=utc(), recovered_from_complete_archive=True)
    atomic_json(PROGRESS, progress)
    print(f'RECOVERED a previously finished archive: {spec["algorithm"]} seed={spec["trial_seed"]}', flush=True)


def init_or_resume(ctx):
    if not WORK.exists():
        WORK.mkdir(parents=True, exist_ok=False)
    else:
        assert WORK.is_dir() and not WORK.is_symlink(), f'STOP: unexpected work path: {WORK}'
    if PROGRESS.exists():
        progress = read_json(PROGRESS)
        assert progress['study_id'] == 'FIX24_S5_25PCT_SOC'
        assert progress['planned'] == 160
        assert progress['manifest_sha256'] == ctx['pinned'][MANIFEST]
        assert progress['analysis_protocol_sha256'] == ctx['pinned'][PROTOCOL]
        assert progress['frozen_source_sha256'] == SOURCE_SHA
        assert progress.get('controller_sha256') == sha(Path(__file__).resolve()), (
            'STOP: campaign controller changed after checkpoint; no silent resume')
        assert progress.get('environment_delta') == ctx['environment_delta'], (
            'STOP: resumed campaign environment differs from recorded kernel exception')
    else:
        assert not any(WORK.iterdir()), f'STOP: unexpected existing files in {WORK}; no overwrites'
        progress = dict(study_id='FIX24_S5_25PCT_SOC', fix='FIX-24', step='117C',
                        created_utc=utc(), planned=160,
                        manifest_sha256=ctx['pinned'][MANIFEST],
                        analysis_protocol_sha256=ctx['pinned'][PROTOCOL],
                        frozen_source_sha256=SOURCE_SHA,
                        controller_sha256=sha(Path(__file__).resolve()),
                        environment_delta=ctx['environment_delta'], records=[])
        atomic_json(PROGRESS, progress)
    all_specs = {ident(r): r for r in ctx['cases']}
    seen = {}
    for rec in progress['records']:
        key = ident(rec)
        assert key in all_specs and key not in seen, f'STOP: bad checkpoint entry {key}'
        spec = all_specs[key]
        wd = workdir_for(spec).resolve()
        assert Path(rec['workdir']).resolve() == wd and wd.is_dir() and not wd.is_symlink()
        for name in ('app.py', 'utp_graph.graphml', 'hotspot_locations.csv'):
            assert sha(wd / name) == sha(SOURCE / name), 'STOP: simulator copy drift'
        if rec['status'] == 'STARTED':
            recover_completed_started(ctx, rec, spec, progress)
        elif rec['status'] != 'VALIDATED':
            raise RuntimeError(f'STOP: existing case {key} status={rec["status"]}; preserve evidence and investigate')
        metrics = validate_case(ctx, spec, Path(rec['archive']))
        compare_record_to_metrics(rec, metrics)
        seen[key] = rec
    print(f'PASS: resume checkpoint validated {len(seen)}/160 existing archives', flush=True)
    return progress, seen


def run_case(ctx, progress, spec):
    wd = workdir_for(spec)
    assert not wd.exists(), f'STOP: workdir already exists (would overwrite): {wd}'
    rec = dict(algorithm=spec['algorithm'], trial_index=int(spec['trial_index']),
               trial_seed=int(spec['trial_seed']), soc=.25, workdir=str(wd.resolve()),
               status='STARTED', started_utc=utc())
    progress['records'].append(rec)
    atomic_json(PROGRESS, progress)
    try:
        shutil.copytree(SOURCE, wd, symlinks=False)
        for name in ('app.py', 'utp_graph.graphml', 'hotspot_locations.csv'):
            assert sha(wd / name) == sha(SOURCE / name)
        cmd = [sys.executable, 'app.py', '--headless', '--scenario', 'S4',
               '--algorithm', spec['algorithm'], '--trial-index', str(spec['trial_index']),
               '--max-sim-s', '14400', '--flush-every', '200', '--depot-layout', 'three',
               '--dispatch-window-s', '30', '--dispatch-urgent-slack-s', '60',
               '--initial-soc-range', '0.25', '0.25',
               '--alpha-energy', '1', '--beta-time', '1', '--soc-cost-weight', '0',
               '--cost-mode', 'normalized', '--energy-reference-wh', '100',
               '--time-reference-s', '600']
        env = os.environ.copy()
        env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONHASHSEED='0',
                   V2_DEPOT_LAYOUT='three', V2_THREE_DEPOT_IDS='P02,P08,P16')
        completed = subprocess.run(cmd, cwd=wd, env=env, capture_output=True,
                                   text=True, timeout=1200, check=False)
        (wd / 'pilot_stdout.txt').write_text(completed.stdout, encoding='utf-8')
        (wd / 'pilot_stderr.txt').write_text(completed.stderr, encoding='utf-8')
        assert completed.returncode == 0, (
            f'Headless simulator exit {completed.returncode}: {completed.stderr[-1600:]}')
        status = ctx['validator'].final_status(completed.stdout)
        assert status['status'] == 'completed' and status['error'] is None
        metrics = validate_case(ctx, spec, status['results_dir'])
        rec.update(metrics)
        rec.update(status='VALIDATED', completed_utc=utc())
        atomic_json(PROGRESS, progress)
        print(f'PASS: {rec["completed"]}/100 completed; '
              f'blocked_pairs={rec["energy_blocked_pair_evaluations"]}; '
              f'blocked_insertions={rec["energy_blocked_insertions"]}; '
              f'battery={rec["battery_Wh"]:.2f}Wh; grid={rec["grid_Wh"]:.2f}Wh; '
              f'deadline_misses={rec["over_300ms"]}', flush=True)
        ensure_protected(ctx)
    except BaseException as exc:
        rec['status'] = 'FAILED' if isinstance(exc, Exception) else 'INTERRUPTED'
        rec['error'] = f'{type(exc).__name__}: {exc}'
        rec['stopped_utc'] = utc()
        atomic_json(PROGRESS, progress)
        print(f'STOP: {rec["error"]}', flush=True)
        print(f'Preserved isolated workdir: {wd}', flush=True)
        raise


def write_csv(progress):
    fields = ['algorithm', 'trial_index', 'trial_seed', 'initial_soc_percent',
              'completed', 'uncompleted', 'energy_blocked_pair_evaluations',
              'energy_blocked_insertions', 'battery_Wh', 'grid_Wh',
              'dispatches', 'over_300ms', 'dispatch_max_ms',
              'canonical_workload_sha256', 'archive']
    assert all(r['status'] == 'VALIDATED' for r in progress['records'])
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for r in sorted(progress['records'], key=lambda q: (ALGORITHMS.index(q['algorithm']), q['trial_index'])):
        writer.writerow({**{field: r[field] for field in fields if field not in ('initial_soc_percent',)},
                         'initial_soc_percent': 25})
    expected_bytes = buffer.getvalue().encode('utf-8')
    if SUMMARY.exists():
        assert SUMMARY.read_bytes() == expected_bytes, 'STOP: existing S5 summary differs; no overwrite'
    else:
        with SUMMARY.open('xb') as out:
            out.write(expected_bytes)


def main():
    ctx = preflight()
    ensure_protected(ctx)
    progress, validated = init_or_resume(ctx)
    for spec in ctx['cases']:
        key = ident(spec)
        if key in validated:
            continue
        n = len(progress['records']) + 1
        print(f'\n===== S5 RUN {n}/160: {spec["algorithm"]} '
              f'seed={spec["trial_seed"]} trial_index={spec["trial_index"]} =====', flush=True)
        run_case(ctx, progress, spec)
    assert len(progress['records']) == 160
    assert {ident(r) for r in progress['records']} == EXPECTED_KEYS
    assert all(r['status'] == 'VALIDATED' for r in progress['records'])
    write_csv(progress)
    ensure_protected(ctx)
    print('\n===== STEP 117C FULL S5 CAMPAIGN SUMMARY =====', flush=True)
    for algorithm in ALGORITHMS:
        group = [r for r in progress['records'] if r['algorithm'] == algorithm]
        assert len(group) == 20
        count = sum(r['completed'] for r in group)
        block = sum(r['energy_blocked_pair_evaluations'] for r in group)
        misses = sum(r['over_300ms'] for r in group)
        print(f'{algorithm:14s}: completed={count}/2000 ({count/20:.2f}%); '
              f'blocked_pairs={block}; >300ms={misses}', flush=True)
    totals = dict(runs=len(progress['records']),
                  completed=sum(r['completed'] for r in progress['records']),
                  requested=16000,
                  blocked_pairs=sum(r['energy_blocked_pair_evaluations'] for r in progress['records']),
                  blocked_insertions=sum(r['energy_blocked_insertions'] for r in progress['records']),
                  dispatches=sum(r['dispatches'] for r in progress['records']),
                  deadline_misses=sum(r['over_300ms'] for r in progress['records']))
    assert 0 <= totals['completed'] <= totals['requested']
    print(f'TOTAL: {totals}', flush=True)
    print('PASS: All 160 S5 archives validated and frozen research evidence preserved', flush=True)
    print('NOTE: No ranking/significance established until separate Step 117D paired analysis.', flush=True)
    print('NOTE: Energy and charging values remain simulation-model predictions, not physical measurements.', flush=True)
    print(f'Checkpoint: {PROGRESS}', flush=True)
    print(f'Run-level summary: {SUMMARY}', flush=True)
    print('===== STEP 117C COMPLETE =====', flush=True)


if __name__ == '__main__':
    main()
