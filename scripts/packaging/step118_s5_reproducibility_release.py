#!/usr/bin/env python3
"""FIX-24 / STEP 118: read-only S5 reproducibility qualification and release.

Run from ~/utp-uber-app using the study's virtual environment. Does NOT run the
simulator, change experiment settings, or edit any previous result. Imports the
exact pinned Step117D verifier and re-checks all 160 archived runs, their
battery ledgers and decision statistics; independently hashes every archived
file. Creates a separate, compact Step118 package, indexing (not embedding) the
160 full raw run archives. Prior 640 experiments remain independently frozen.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

ROOT = Path('research_revision_v2').resolve()
SOURCE = ROOT / 'step106b_frozen_candidate_20261029T174020Z/simulator/app.py'
FROZEN_SHA = '7b52bb550414469937da3098f91313b366137d43bd48b8465b840a97bffeb0a0'
STAGE_SCRIPT = Path('step117d_s5_paired_analysis.py').resolve()
STAGE_SCRIPT_SHA = '955fdc777fcd76fa9229032f022be54385bdb63caedb391d455d9da6757f443c'
ANALYSIS_DIR = ROOT / 'step117d_s5_paired_analysis'
ANALYSIS_ZIP = ANALYSIS_DIR / 'step117d_shareable_analysis.zip'
PROTOCOL_DIR = ROOT / 'step117b_s5_decision_and_protocol'
CAMPAIGN_DIR = ROOT / 'step117c_s5_full160_campaign'
ORIGINAL_CAMPAIGN = ROOT / 'step108_full640_unified_campaign/campaign_640_progress.json'
STEP115_ZIP = ROOT / 'step115_reproducibility_release/step115_compact_evidence_release.zip'
DEST = ROOT / 'step118_s5_reproducibility_release'
ALGORITHMS = ('greedy', 'hungarian', 'mcmf', 'auction', 'random',
              'roundrobin', 'greedy_minmax', 'annealing')
EXPECTED_KEYS = {(a, t) for a in ALGORITHMS for t in range(20)}


def require(condition, detail):
    if not condition:
        raise RuntimeError('STOP: ' + str(detail))


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), f'missing/symlink file {path}')
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def jbytes(payload):
    return (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False,
                       allow_nan=False) + '\n').encode('utf-8')


def csv_file(headers, rows):
    buf = io.StringIO(newline='')
    writer = csv.DictWriter(buf, fieldnames=headers, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode('utf-8')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def stage_module():
    require(sha(STAGE_SCRIPT) == STAGE_SCRIPT_SHA,
            'Step117D analyzer script differs from approved hash')
    spec = importlib.util.spec_from_file_location('fix24_step117d_ref', STAGE_SCRIPT)
    require(spec is not None and spec.loader is not None,
            'could not load pinned Step117D analysis')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_analysis_package():
    require(ANALYSIS_ZIP.is_file() and not ANALYSIS_ZIP.is_symlink(),
            f'missing Step117D release {ANALYSIS_ZIP}')
    with ZipFile(ANALYSIS_ZIP, 'r') as z:
        names = z.namelist()
        require(len(names) == len(set(names)), 'duplicate ZIP member names')
        require('package_file_sha256.json' in names, 'ZIP index missing')
        index = json.loads(z.read('package_file_sha256.json'))
        require(set(names) == set(index) | {'package_file_sha256.json'},
                'Step117D ZIP member inventory differs from index')
        for name, digest in index.items():
            require(name and not name.startswith('/') and '..' not in Path(name).parts,
                    f'unsafe Step117D ZIP path {name}')
            content = z.read(name)
            require(sha_bytes(content) == digest, f'Step117D member altered {name}')
            local = ANALYSIS_DIR / name
            require(sha(local) == digest, f'Step117D analysis output altered {name}')
        return {name: z.read(name) for name in index}, len(index)


def check_reproduced_csv(stage, content, rows):
    parsed = csv.DictReader(io.StringIO(content.decode('utf-8-sig')))
    headers = parsed.fieldnames
    require(headers is not None and len(headers) > 1, 'analysis CSV header missing')
    expected = stage.csv_content(headers, rows)
    require(expected == content, 'Step117D recorded analysis differs from recomputed values')


def archive_inventory(progress):
    """Second, independent full-file SHA256 pass; archive data is never copied."""
    paths = set()
    file_rows = []
    run_rows = []
    for index, rec in enumerate(sorted(progress['records'], key=lambda r:(
            ALGORITHMS.index(r['algorithm']), int(r['trial_index']))), 1):
        a = rec['algorithm']
        t = int(rec['trial_index'])
        require((a,t) in EXPECTED_KEYS, f'unexpected archive {a}/{t}')
        require(int(rec['trial_seed']) == 1001 + 1000*t and rec['status'] == 'VALIDATED',
                f'bad campaign state for {a}/{t}')
        archive = Path(rec['archive']).resolve()
        require(archive.is_dir() and not archive.is_symlink() and
                archive.is_relative_to(CAMPAIGN_DIR.resolve()),
                f'archive outside S5 workdir {archive}')
        require(archive not in paths, f'duplicate archived directory {archive}')
        paths.add(archive)
        checks = rec['archive_checksums']
        require(isinstance(checks, dict) and len(checks) >= 13,
                f'expected checksums missing in {a}/{t}')
        actual = {}
        for path in sorted(archive.rglob('*')):
            require(not path.is_symlink(), f'archive contains symbolic link {path}')
            if path.is_dir():
                continue
            require(path.is_file(), f'unknown archive entry {path}')
            relative = path.relative_to(archive).as_posix()
            require(not relative.startswith('/') and '..' not in Path(relative).parts,
                    f'unsafe archive member {relative}')
            digest = sha(path)
            actual[relative] = digest
            file_rows.append(dict(algorithm=a, trial_index=t, trial_seed=1001+1000*t,
                                  archived_file=relative, bytes=path.stat().st_size,
                                  sha256=digest))
        for name, digest in checks.items():
            require(actual.get(name) == digest,
                    f'archived checksum changed in {a}/{t}/{name}')
        require(len(actual) >= len(checks), 'archived file inventory incomplete')
        # Content-based file tree digest is stable if the archive is relocated.
        tree_payload = jbytes([dict(filename=k, sha256=v) for k,v in sorted(actual.items())])
        run_rows.append(dict(algorithm=a, trial_index=t, trial_seed=1001+1000*t,
                             archive_relative_to_research_revision_v2=str(archive.relative_to(ROOT)),
                             verified_files=len(actual), archived_tree_sha256=sha_bytes(tree_payload),
                             canonical_workload_sha256=rec['canonical_workload_sha256'],
                             completed=rec['completed'], uncompleted=rec['uncompleted'],
                             energy_blocked_pairs=rec['energy_blocked_pair_evaluations'],
                             battery_Wh=rec['battery_Wh'], grid_Wh=rec['grid_Wh'],
                             dispatches=rec['dispatches'], over_300ms=rec['over_300ms']))
        if index % 40 == 0:
            print(f'Independent full-file checksum inventory: {index}/160 archived runs',
                  flush=True)
    require(len(paths)==len(run_rows)==160 and
            {(r['algorithm'],r['trial_index']) for r in run_rows} == EXPECTED_KEYS,
            'missing, duplicated, or unexpected S5 run')
    return run_rows, file_rows


def make_package(items):
    items = dict(items)
    require('release_file_sha256.json' not in items, 'reserved package path')
    index = {name:sha_bytes(data) for name,data in sorted(items.items())}
    items['release_file_sha256.json'] = jbytes(index)
    data = io.BytesIO()
    with ZipFile(data, 'w', compression=ZIP_DEFLATED, compresslevel=9) as z:
        for name, payload in sorted(items.items()):
            require(name and not name.startswith('/') and '..' not in Path(name).parts,
                    f'unsafe release name {name}')
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            z.writestr(info, payload)
    return data.getvalue()


def write_new_or_same(path, data):
    path = Path(path)
    if path.exists():
        require(path.is_file() and not path.is_symlink() and path.read_bytes() == data,
                f'existing Step118 output differs; will not overwrite: {path}')
    else:
        with path.open('xb') as stream:
            stream.write(data)


def main():
    print('===== FIX-24 / STEP 118: S5 REPRODUCIBILITY QUALIFICATION =====', flush=True)
    require(sha(SOURCE) == FROZEN_SHA, 'frozen FIX-19 source changed')
    initial = {p:sha(p) for p in (
        ORIGINAL_CAMPAIGN, STEP115_ZIP,
        PROTOCOL_DIR / 'step117b_s5_160_manifest.csv',
        PROTOCOL_DIR / 'step117b_s5_analysis_protocol.json',
        CAMPAIGN_DIR / 'step117c_progress.json',
        CAMPAIGN_DIR / 'step117c_run_level_160.csv',
        ANALYSIS_ZIP,
    )}
    stage = stage_module()
    checked, evidence = stage.archive_checked_rows()
    _, summary, contrasts = stage.summarize(checked)
    context = stage.old_s4_context({(r['algorithm'],int(r['trial_index'])):r for r in checked})
    require(sum(r['completed'] for r in summary) == 14982 and
            sum(r['energy_blocked_pair_evaluations'] for r in summary) == 148625 and
            sum(r['over_300ms'] for r in summary) == 389 and
            sum(r['dispatches'] for r in summary) == 10750,
            'S5 reconciliation totals differ')
    require(sum(r['s4_full_soc_completed'] for r in context) == 15962,
            'original S4 context differs')
    require(len(contrasts)==7 and
            {r['algorithm'] for r in contrasts} == set(ALGORITHMS)-{'hungarian'},
            'locked paired inference family differs')
    significant = {r['algorithm'] for r in contrasts if r['significant_at_0_05_holm']}
    require(significant == {'random', 'roundrobin'},
            'locked paired inference conclusions differ')
    release_items, member_count = check_analysis_package()
    check_reproduced_csv(stage, release_items['step117d_s5_algorithm_summary.csv'], summary)
    check_reproduced_csv(stage, release_items['step117d_s5_paired_vs_hungarian.csv'], contrasts)
    check_reproduced_csv(stage, release_items['step117d_s5_full_soc_s4_context.csv'], context)
    recorded_report = json.loads(release_items['step117d_s5_integrity_audit.json'])
    require(recorded_report['runs_validated'] == 160 and
            recorded_report['completed'] == 14982 and
            recorded_report['dispatch_invocations'] == 10750 and
            recorded_report['over_300ms_dispatch_invocations'] == 389,
            'Step117D recorded release totals differ')
    require(recorded_report['evidence'] == evidence,
            'Step117D recorded evidence provenance differs')
    progress = read_json(CAMPAIGN_DIR / 'step117c_progress.json')
    indexed_runs, indexed_files = archive_inventory(progress)
    require(all(r['verified_files'] >= 13 for r in indexed_runs),
            'run archive has fewer than 13 files')
    require(sum(r['completed'] for r in indexed_runs)==14982 and
            sum(r['over_300ms'] for r in indexed_runs)==389,
            'run-file index mismatches summary')
    # Required every case's source version and workload match original full-SOC S4.
    require(len({r['canonical_workload_sha256'] for r in indexed_runs})==20,
            'expected exactly 20 seed-matched workloads')
    run_index_csv = csv_file(list(indexed_runs[0]), indexed_runs)
    file_index_csv = csv_file(list(indexed_files[0]), indexed_files)
    provenance = dict(
        study='FIX-24 S5 supplementary 25%-SOC stress test; S4 CLI workload',
        frozen_source_sha256=FROZEN_SHA, phase='post-frozen FIX-19 supplementary',
        locked_protocol_sha256=initial[PROTOCOL_DIR / 'step117b_s5_analysis_protocol.json'],
        locked_manifest_sha256=initial[PROTOCOL_DIR / 'step117b_s5_160_manifest.csv'],
        original_step115_release_sha256=initial[STEP115_ZIP],
        original_640_campaign_checkpoint_sha256=initial[ORIGINAL_CAMPAIGN],
        step117c_campaign_checkpoint_sha256=initial[CAMPAIGN_DIR / 'step117c_progress.json'],
        step117c_run_level_sha256=initial[CAMPAIGN_DIR / 'step117c_run_level_160.csv'],
        step117d_analysis_script_sha256=STAGE_SCRIPT_SHA,
        step117d_analysis_release_sha256=initial[ANALYSIS_ZIP],
        step117d_release_verified_files=member_count,
        s5_runs=160, paired_seed_sets=20, requests=16000,
        completed=14982, uncompleted=1018,
        battery_constraint_blocked_pair_evaluations=148625,
        blocked_candidate_insertions=245775,
        measured_dispatches=10750, over_300ms=389,
        indexed_raw_archived_files=len(indexed_files),
        kernel_delta=evidence['environment_delta'],
        significant_vs_hungarian_after_holm=sorted(significant),
        calibration_status='No real-vehicle battery energy calibration',
        hard_real_time_status='Not demonstrated; empirical 300ms budget violations',
        raw_archive_delivery='Not embedded; preserved externally in 160 existing run directories',
    )
    readme = f'''# FIX-24 / Step 118: S5 Supplementary Reproducibility Release

Study scope: eight assignment algorithms, 20 paired seeds, 100 requests per seed,
12 UGVs. The **supplementary S5 label** means the original frozen S4 workload
with **initial battery SOC = 25%** and **reserve = 20%**. The physical vehicle was
not available for calibration. S5 stress assumptions are not field-validated.

## Verified result

- Validated 160/160 S5 run archives against source, workload, request outcomes,
  vehicle reserves, battery accounting, charging ledger and timing logs.
- Completed 14,982/16,000 offered trips; 148,625 energy-blocked **candidate
  evaluations**, not unique rejected passengers.
- Measured 389/10,750 dispatches above the 300 ms budget (3.62%).
- MCMF has the highest descriptive fulfillment (98.40%), without a statistically
  established improvement over Hungarian in the seven prespecified comparisons.
- Random and Round-Robin are significantly inferior to Hungarian on completed
  trips after Holm adjustment. Do not infer superiority for other algorithms.
- The post-pilot S5 analysis protocol was locked before the full 160-run study.
- Hardware/kernel difference from the original benchmark is documented.

## Contents

- `protocol/`: the original locked Step117B protocol and 160-run manifest.
- `analysis/step117d_shareable_analysis.zip`: primary results, comparisons,
  and primary-source provenance verified independently by this controller.
- `analysis/`: the three principal S5 analysis CSV tables and manuscript text.
- `archive_index_160.csv`: paths and content-tree SHA256 per validated S5 run.
- `archive_file_checksums_160.csv`: one SHA256 for each archived file, verified
  against stored experiment checkpoints when those hashes were available.
- `step118_release_summary.json`: counts, source identity and dependency hashes.

This compact evidence ZIP **does not contain all 160 raw run archives**. Keep
`research_revision_v2/step117c_s5_full160_campaign/` unchanged and retain the
original full 640-run archive separately. To reproduce, use the frozen FIX-19
simulator, the exact locked S5 manifest, and the recorded Python/dependency
versions, noting the documented Linux kernel revision.

The file SHA256 index `release_file_sha256.json` authenticates package members;
it is not an independent cryptographic signature. Do not report physical energy
accuracy or hard real-time guarantees based on these simulations.

Indexed raw archive files: **{len(indexed_files):,}**.
'''
    files = {
        'README.md': readme.encode('utf-8'),
        'protocol/step117b_s5_analysis_protocol.json': (
            PROTOCOL_DIR/'step117b_s5_analysis_protocol.json').read_bytes(),
        'protocol/step117b_s5_160_manifest.csv': (
            PROTOCOL_DIR/'step117b_s5_160_manifest.csv').read_bytes(),
        'analysis/step117d_shareable_analysis.zip': ANALYSIS_ZIP.read_bytes(),
        'analysis/Step117D_S5_Manuscript_Results.md': release_items['Step117D_S5_Manuscript_Results.md'],
        'analysis/step117d_s5_algorithm_summary.csv': release_items['step117d_s5_algorithm_summary.csv'],
        'analysis/step117d_s5_paired_vs_hungarian.csv': release_items['step117d_s5_paired_vs_hungarian.csv'],
        'analysis/step117d_s5_full_soc_s4_context.csv': release_items['step117d_s5_full_soc_s4_context.csv'],
        'archive_index_160.csv': run_index_csv,
        'archive_file_checksums_160.csv': file_index_csv,
        'step118_release_summary.json': jbytes(provenance),
    }
    # Final read-only recheck before making the new release directory.
    for path, digest in initial.items():
        require(sha(path)==digest, f'upstream study changed during audit: {path}')
    require(sha(SOURCE)==FROZEN_SHA and sha(STAGE_SCRIPT)==STAGE_SCRIPT_SHA,
            'protected source or validator changed during audit')
    package = make_package(files)
    with ZipFile(io.BytesIO(package), 'r') as z:
        index = json.loads(z.read('release_file_sha256.json'))
        require(set(z.namelist()) == set(index) | {'release_file_sha256.json'},
                'new ZIP inventory inconsistent')
        for name, expected in index.items():
            require(sha_bytes(z.read(name))==expected, f'new ZIP member mismatch {name}')
    require(not DEST.is_symlink(), 'destination symlink detected')
    DEST.mkdir(parents=True, exist_ok=True)
    for name,data in files.items():
        path = DEST/name
        path.parent.mkdir(parents=True, exist_ok=True)
        write_new_or_same(path,data)
    write_new_or_same(DEST / 'step118_s5_compact_evidence_release.zip', package)
    print('\n===== STEP 118 FINAL S5 REPRODUCIBILITY INVENTORY =====', flush=True)
    print('Validated S5 runs: 160/160; paired seeds: 20/20', flush=True)
    print(f'Indexed original S5 archive files: {len(indexed_files):,}', flush=True)
    print('Requests fulfilled: 14,982/16,000; energy-blocked pair evaluations: 148,625', flush=True)
    print('Dispatch budget violations: 389/10,750 (3.62%)', flush=True)
    print('Primary paired comparisons: Random and Round-Robin below Hungarian after Holm correction', flush=True)
    print('MCMF: highest descriptive fulfillment, NOT established superior by the locked test', flush=True)
    print('Sources, manifest and original 640-run campaign: UNCHANGED', flush=True)
    print('No simulations started; original 160 raw archives: INDEXED, NOT REPACKED', flush=True)
    print(f'Release ZIP: {DEST / "step118_s5_compact_evidence_release.zip"}', flush=True)
    print('===== STEP 118 COMPLETE =====', flush=True)


if __name__ == '__main__':
    main()
