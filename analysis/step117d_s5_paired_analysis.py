#!/usr/bin/env python3
"""FIX-24 / STEP 117D: READ-ONLY S5 archive verification + locked paired inference.

Usage from ~/utp-uber-app:
  PYTHONDONTWRITEBYTECODE=1 ./venv/bin/python -u step117d_s5_paired_analysis.py

Reads the Step117B locked protocol, the Step117C validated 160-run checkpoint,
all 160 archived outcomes (using the exact pinned Step117A and Step117C validators),
and the Step115 accepted S4 evidence. Makes NO simulator calls or changes to
historical artifacts. Creates only a new Step117D analysis directory.

Primary endpoint: paired completed-request counts per 100 requests per seed.
Seven comparisons against Hungarian, exact two-sided sign-flip tests, Holm
family-wise correction across seven. Unadjusted 95% paired t intervals are
shown as uncertainty descriptions, not simultaneous CIs.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path('research_revision_v2').resolve()
FREEZE = ROOT / 'step106b_frozen_candidate_20261029T174020Z'
DECISION = ROOT / 'step117b_s5_decision_and_protocol'
WORK = ROOT / 'step117c_s5_full160_campaign'
DEST = ROOT / 'step117d_s5_paired_analysis'
RELEASE = ROOT / 'step115_reproducibility_release/step115_compact_evidence_release.zip'
CONTROLLER = Path('step117c_s5_full160_campaign_kernelguard_v2.py').resolve()
CONTROLLER_SHA = '3f155ef34960bac970d242dbf6da4043b8aaae9400c480f189d6c9e1f24b436f'
PROTOCOL = DECISION / 'step117b_s5_analysis_protocol.json'
MANIFEST = DECISION / 'step117b_s5_160_manifest.csv'
PROGRESS = WORK / 'step117c_progress.json'
RUN_CSV = WORK / 'step117c_run_level_160.csv'
ALGORITHMS = ('greedy', 'hungarian', 'mcmf', 'auction', 'random',
              'roundrobin', 'greedy_minmax', 'annealing')
N = 20
SEEDS = tuple(1001 + 1000 * i for i in range(N))
T_CRIT_DF19_95 = 2.093024054408263  # two-sided, 95%, df=19


def sha(path):
    p = Path(path)
    assert p.is_file() and not p.is_symlink(), f'STOP: missing/symlink: {p}'
    h = hashlib.sha256()
    with p.open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def csv_rows(path):
    with Path(path).open('r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def csv_bytes(data):
    return list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))


def csv_content(fields, rows):
    f = io.StringIO(newline='')
    writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore', lineterminator='\n')
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return f.getvalue().encode('utf-8')


def json_content(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n').encode('utf-8')


def finite(value):
    x = float(value)
    assert math.isfinite(x), f'STOP: nonfinite value {value}'
    return x


def exact_signflip(values):
    """Exact, two-sided paired sign-flip test for integer completion differences.

    Enumerates the full 2**20 sign space by dynamic-programming sum counting.
    Zero differences remain tied and contribute two identical sign outcomes.
    It is a randomization test under within-pair sign-exchangeability; cannot
    itself prove algorithm-level causal superiority beyond these workloads.
    """
    vals = tuple(int(v) for v in values)
    assert len(vals) == N
    dist = {0: 1}
    for v in vals:
        updated = Counter()
        for total, count in dist.items():
            updated[total + v] += count
            updated[total - v] += count
        dist = updated
    assert sum(dist.values()) == (1 << N)
    cutoff = abs(sum(vals))
    extreme = sum(count for total, count in dist.items() if abs(total) >= cutoff)
    return extreme / (1 << N), sum(v > 0 for v in vals), sum(v == 0 for v in vals), sum(v < 0 for v in vals)


def paired_t_interval(values):
    vals = [finite(v) for v in values]
    assert len(vals) == N
    avg = statistics.mean(vals)
    sd = statistics.stdev(vals)
    radius = T_CRIT_DF19_95 * sd / math.sqrt(N)
    return (avg - radius, avg + radius)


def holm_adjust(pvals):
    m = len(pvals)
    order = sorted(range(m), key=lambda i: (pvals[i], i))
    adjusted = [None] * m
    running = 0.0
    for position, index in enumerate(order):
        running = max(running, min(1.0, (m - position) * pvals[index]))
        adjusted[index] = running
    assert all(x is not None and 0 <= x <= 1 for x in adjusted)
    return adjusted


def summarize(rows):
    """Pure results analysis; rows must already have been archive-validated."""
    mapped = {}
    for r in rows:
        a, t = r['algorithm'], int(r['trial_index'])
        assert a in ALGORITHMS and 0 <= t < N and (a, t) not in mapped
        assert int(r['trial_seed']) == SEEDS[t]
        assert 0 <= int(r['completed']) <= 100 and int(r['uncompleted']) == 100 - int(r['completed'])
        assert int(r['initial_soc_percent']) == 25
        assert finite(r['battery_Wh']) >= 0 and finite(r['grid_Wh']) >= 0
        assert int(r['over_300ms']) >= 0 and 0 <= int(r['over_300ms']) <= int(r['dispatches'])
        mapped[a, t] = r
    assert len(mapped) == N * len(ALGORITHMS)
    assert set(mapped) == {(a, t) for a in ALGORITHMS for t in range(N)}

    summary = []
    for a in ALGORITHMS:
        group = [mapped[a, t] for t in range(N)]
        comp = [int(r['completed']) for r in group]
        energy = [finite(r['battery_Wh']) for r in group]
        grid = [finite(r['grid_Wh']) for r in group]
        dispatches = sum(int(r['dispatches']) for r in group)
        misses = sum(int(r['over_300ms']) for r in group)
        completed = sum(comp)
        failures = Counter()
        for r in group:
            reasons = r.get('failure_reasons', {})
            assert sum(int(v) for v in reasons.values()) == int(r['uncompleted'])
            failures.update({k: int(v) for k, v in reasons.items()})
        summary.append(dict(
            algorithm=a, runs=N, requested=100 * N, completed=completed,
            uncompleted=100 * N - completed, fulfillment_pct=100 * completed / (100 * N),
            completed_per_seed_mean=statistics.mean(comp),
            completed_per_seed_sd=statistics.stdev(comp),
            gross_battery_Wh_per_run_mean=statistics.mean(energy),
            grid_import_Wh_per_run_mean=statistics.mean(grid),
            gross_battery_Wh_per_completed_request=sum(energy) / completed if completed else None,
            energy_blocked_pair_evaluations=sum(int(r['energy_blocked_pair_evaluations']) for r in group),
            energy_blocked_insertions=sum(int(r['energy_blocked_insertions']) for r in group),
            dispatches=dispatches, over_300ms=misses,
            over_300ms_pct=(100 * misses / dispatches) if dispatches else None,
            failure_reasons=dict(sorted(failures.items())),
        ))

    control = [int(mapped['hungarian', t]['completed']) for t in range(N)]
    infer = []
    for a in ALGORITHMS:
        if a == 'hungarian':
            continue
        differences = [int(mapped[a, t]['completed']) - control[t] for t in range(N)]
        p, wins, ties, losses = exact_signflip(differences)
        lo, hi = paired_t_interval(differences)
        infer.append(dict(
            algorithm=a, comparator='hungarian', seeds=N,
            mean_diff_completed_per_100_requests=statistics.mean(differences),
            total_diff_completed_across_20_seeds=sum(differences),
            paired_t_95ci_low_unadjusted=lo,
            paired_t_95ci_high_unadjusted=hi,
            pairs_better=wins, pairs_equal=ties, pairs_worse=losses,
            two_sided_exact_signflip_p=p,
            paired_differences_by_seed=differences,
        ))
    holm = holm_adjust([r['two_sided_exact_signflip_p'] for r in infer])
    for r, adjusted in zip(infer, holm):
        r['holm_p_7_fulfillment_tests'] = adjusted
        r['significant_at_0_05_holm'] = adjusted < .05
    assert sum(r['completed'] for r in summary) == sum(int(r['completed']) for r in rows)
    return mapped, summary, infer


def archive_checked_rows():
    """Reads only: call the exact pinned campaign preflight and verification."""
    assert sha(CONTROLLER) == CONTROLLER_SHA, 'STOP: Step117C controller does not match pinned SHA256'
    spec = importlib.util.spec_from_file_location('fix24_step117c_reference', CONTROLLER)
    assert spec is not None and spec.loader is not None
    controller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controller)
    ctx = controller.preflight()
    controller.ensure_protected(ctx)
    progress_sha = sha(PROGRESS)
    run_sha = sha(RUN_CSV)
    protocol_sha = sha(PROTOCOL)
    manifest_sha = sha(MANIFEST)
    original_sha = sha(RELEASE)
    protocol = read_json(PROTOCOL)
    for term in ('Seven comparisons', 'paired per-seed'):
        assert any(term.lower() in x.lower() for x in protocol['analysis_plan']), f'STOP: missing locked method: {term}'
    assert protocol['primary_operational_outcome'].startswith('completed requests')
    assert protocol['campaign_planned_runs'] == 160
    assert protocol['algorithms'] == list(ALGORITHMS)
    assert progress_sha and run_sha and protocol_sha and manifest_sha
    progress = read_json(PROGRESS)
    assert progress['study_id'] == 'FIX24_S5_25PCT_SOC'
    assert progress['planned'] == 160 and len(progress['records']) == 160
    assert progress['manifest_sha256'] == manifest_sha
    assert progress['analysis_protocol_sha256'] == protocol_sha
    assert progress['frozen_source_sha256'] == controller.SOURCE_SHA
    assert progress['controller_sha256'] == CONTROLLER_SHA
    assert progress['environment_delta'] == ctx['environment_delta']
    assert all(r['status'] == 'VALIDATED' for r in progress['records'])

    specs = {controller.ident(r): r for r in ctx['cases']}
    assert len(specs) == 160
    stored = {controller.ident(r): r for r in progress['records']}
    assert set(stored) == set(specs)
    summary_rows = csv_rows(RUN_CSV)
    actual_csv = {(r['algorithm'], int(r['trial_index'])): r for r in summary_rows}
    assert len(summary_rows) == len(actual_csv) == 160
    assert set(actual_csv) == set(specs)
    checked = []
    for index, (a, t) in enumerate(((a, t) for a in ALGORITHMS for t in range(N)), 1):
        spec = specs[a, t]
        rec = stored[a, t]
        assert rec['algorithm'] == a and int(rec['trial_index']) == t
        assert int(rec['trial_seed']) == SEEDS[t]
        expected_workdir = controller.workdir_for(spec).resolve()
        assert Path(rec['workdir']).resolve() == expected_workdir
        assert expected_workdir.is_dir() and not expected_workdir.is_symlink()
        for name in ('app.py', 'utp_graph.graphml', 'hotspot_locations.csv'):
            assert sha(expected_workdir / name) == sha(controller.SOURCE / name)
        metrics = controller.validate_case(ctx, spec, Path(rec['archive']))
        controller.compare_record_to_metrics(rec, metrics)
        line = actual_csv[a, t]
        assert int(line['trial_seed']) == SEEDS[t]
        assert int(line['initial_soc_percent']) == 25
        for field, ref_field in (
            ('completed','completed'), ('uncompleted','uncompleted'),
            ('energy_blocked_pair_evaluations','energy_blocked_pair_evaluations'),
            ('energy_blocked_insertions','energy_blocked_insertions'),
            ('dispatches','dispatches'), ('over_300ms','over_300ms'),
        ):
            assert int(line[field]) == int(rec[ref_field]), f'STOP: run CSV differs: {a} seed={SEEDS[t]} {field}'
        for field in ('battery_Wh','grid_Wh'):
            assert math.isclose(finite(line[field]), finite(rec[field]), abs_tol=.001, rel_tol=0), f'STOP: CSV differs: {a}/{t} {field}'
        assert line['canonical_workload_sha256'] == metrics['canonical_workload_sha256']
        assert Path(line['archive']).resolve() == Path(rec['archive']).resolve()
        checked.append({**line, 'failure_reasons': metrics['failure_reasons']})
        if index % 40 == 0:
            print(f'Archive reconciliation: {index}/160 validated', flush=True)
    controller.ensure_protected(ctx)
    assert sha(PROGRESS) == progress_sha, 'STOP: checkpoint changed during analysis'
    assert sha(RUN_CSV) == run_sha, 'STOP: run-level CSV changed during analysis'
    assert sha(PROTOCOL) == protocol_sha and sha(MANIFEST) == manifest_sha
    assert sha(RELEASE) == original_sha
    return checked, dict(
        controller_sha256=CONTROLLER_SHA, frozen_source_sha256=controller.SOURCE_SHA,
        progress_sha256=progress_sha, run_level_csv_sha256=run_sha,
        locked_protocol_sha256=protocol_sha, locked_manifest_sha256=manifest_sha,
        step115_evidence_zip_sha256=original_sha,
        environment_delta=ctx['environment_delta'],
    )


def old_s4_context(mapped):
    """Context only. Identical source/workloads but different initial SOC and kernel."""
    with ZipFile(RELEASE) as z:
        hashes = json.loads(z.read('release_file_sha256.json'))
        assert set(z.namelist()) == set(hashes) | {'release_file_sha256.json'}
        for name, digest in hashes.items():
            assert digest_bytes(z.read(name)) == digest
        rows = csv_bytes(z.read('analysis/run_level_640.csv'))
    filtered = {}
    for r in rows:
        if r['scenario'] != 'S4':
            continue
        a, t = r['algorithm'], int(r['trial_index'])
        assert (a, t) not in filtered and a in ALGORITHMS and 0 <= t < N
        assert int(r['trial_seed']) == SEEDS[t]
        assert int(r['requests']) == 100
        filtered[a, t] = r
    assert set(filtered) == set(mapped)
    result = []
    for a in ALGORITHMS:
        baseline = sum(int(filtered[a, t]['completed']) for t in range(N))
        stressed = sum(int(mapped[a, t]['completed']) for t in range(N))
        result.append(dict(
            algorithm=a, s4_full_soc_completed=baseline,
            s5_25pct_soc_completed=stressed,
            paired_mean_change_in_completed_per_100=(stressed - baseline)/N,
            s4_full_soc_mean_battery_Wh_per_run=statistics.mean(finite(filtered[a, t]['battery_Wh']) for t in range(N)),
            s5_25pct_soc_mean_battery_Wh_per_run=statistics.mean(finite(mapped[a, t]['battery_Wh']) for t in range(N)),
        ))
    return result


def article(summary, infer, context, provenance):
    by_name = {r['algorithm']:r for r in summary}
    total_completed = sum(r['completed'] for r in summary)
    total_dispatches = sum(r['dispatches'] for r in summary)
    total_misses = sum(r['over_300ms'] for r in summary)
    total_blocked = sum(r['energy_blocked_pair_evaluations'] for r in summary)
    total_insertions = sum(r['energy_blocked_insertions'] for r in summary)
    lines = [
        '# FIX-24 / Step 117D — Battery-Constrained Assignment: Locked Paired Analysis',
        '',
        '**Study:** Supplementary S5 (actual frozen CLI scenario S4 with 25% initial SOC), 12 vehicles, 100 requests per seed, eight algorithms × 20 paired seeds. The original 640 S1–S4 runs are unchanged.',
        '',
        f'**Independent archive qualification:** 160/160 verified; {total_completed:,}/16,000 completed ({100*total_completed/16000:.3f}%); {total_blocked:,} energy-blocked candidate-pair evaluations and {total_insertions:,} rejected insertions.',
        f'**Dispatch timing:** {total_misses:,} / {total_dispatches:,} complete dispatches exceeded 300 ms ({100*total_misses/total_dispatches:.3f}%). This is empirical soft-deadline evidence, not a hard real-time guarantee.',
        '',
        '## Descriptive fulfillment and energy',
        '',
        '| Algorithm | Completed / 2,000 | Fulfillment | Battery Wh/run | Grid Wh/run | Blocked pair evaluations | >300ms / dispatches |',
        '|---|---:|---:|---:|---:|---:|---:|',
    ]
    for r in summary:
        lines.append(f'| {r["algorithm"]} | {r["completed"]:,} | {r["fulfillment_pct"]:.2f}% | '
                     f'{r["gross_battery_Wh_per_run_mean"]:.1f} | {r["grid_import_Wh_per_run_mean"]:.1f} | '
                     f'{r["energy_blocked_pair_evaluations"]:,} | {r["over_300ms"]} / {r["dispatches"]} |')
    lines += [
        '', '## Locked primary comparisons: algorithm minus Hungarian', '',
        'Exact two-sided paired sign-flip tests over the 20 per-seed **completion-count differences**, Holm-adjusted across the seven comparisons specified in Step 117B. Positive values favor the named algorithm. Reported 95% Student-t intervals are unadjusted, based on 20 paired differences and do not provide simultaneous multiplicity coverage.',
        '',
        '| Algorithm | Mean completed difference per 100 | Unadjusted 95% CI | Better / equal / worse seeds | Exact p | Holm p |',
        '|---|---:|---|---:|---:|---:|',
    ]
    for r in infer:
        lines.append(f'| {r["algorithm"]} | {r["mean_diff_completed_per_100_requests"]:+.3f} | '
                     f'[{r["paired_t_95ci_low_unadjusted"]:+.3f}, {r["paired_t_95ci_high_unadjusted"]:+.3f}] | '
                     f'{r["pairs_better"]}/{r["pairs_equal"]}/{r["pairs_worse"]} | '
                     f'{r["two_sided_exact_signflip_p"]:.6g} | {r["holm_p_7_fulfillment_tests"]:.6g} |')
    lines += ['', '## Original S4 full-SOC context (descriptive only)', '',
              '| Algorithm | Full-SOC S4 completed | S5 25%-SOC completed | Mean paired change in completions per seed |',
              '|---|---:|---:|---:|']
    for r in context:
        lines.append(f'| {r["algorithm"]} | {r["s4_full_soc_completed"]} | '
                     f'{r["s5_25pct_soc_completed"]} | {r["paired_mean_change_in_completed_per_100"]:+.2f} |')
    sorted_primary = sorted(summary, key=lambda r:r['completed'], reverse=True)
    lines += [
        '', '## Interpretation for manuscript', '',
        f'The largest descriptive fulfillment was obtained by {sorted_primary[0]["algorithm"]} '
        f'({sorted_primary[0]["completed"]:,}/2,000), followed by {sorted_primary[1]["algorithm"]} '
        f'({sorted_primary[1]["completed"]:,}/2,000). This is a descriptive ranking only; '
        'formal comparative conclusions must be based on the prespecified paired contrasts.',
        '',
        'Energy-blocked pair evaluations refer to repeated candidate vehicle–request assessments, **not** distinct passengers refused service. Lower total battery withdrawal can result from serving fewer passengers; interpret gross battery withdrawal, grid imports, and completed requests jointly.',
        '',
        'This protocol was locked **after** the exploratory two-seed SOC pilot, before the 160-run campaign. The 25% battery condition is an intentionally harsh simulation stress case, not an empirically validated operating distribution. The full-SOC S4 comparison changes initial battery conditions and was run with a different Linux kernel revision (7.0.0-38 vs 7.0.0-42); avoid claiming causal timing effects from that comparison.',
        '',
        'The physical buggy was unavailable during the experiments. Reported battery and charging values remain model predictions, not measured or calibrated vehicle energy. Observed dispatch budget violations prevent a hard real-time execution guarantee.',
        '',
        '## Reproducibility boundaries', '',
        f'- Frozen FIX-19 source SHA256: `{provenance["frozen_source_sha256"]}`',
        f'- Locked Step 117B protocol SHA256: `{provenance["locked_protocol_sha256"]}`',
        f'- Validated Step 117C checkpoint SHA256: `{provenance["progress_sha256"]}`',
        '- The compact results ZIP indexes analyzed evidence; all 160 complete raw run archives should be preserved separately.',
        '',
    ]
    return ('\n'.join(lines)).encode('utf-8')


def make_zip(files):
    contents = dict(files)
    contents['package_file_sha256.json'] = json_content({k:digest_bytes(v) for k,v in sorted(files.items())})
    data = io.BytesIO()
    with ZipFile(data, 'w', compression=ZIP_DEFLATED, compresslevel=9) as package:
        for name in sorted(contents):
            assert not name.startswith('/') and '..' not in Path(name).parts
            info = ZipInfo(name, date_time=(1980,1,1,0,0,0))
            info.compress_type = ZIP_DEFLATED
            package.writestr(info, contents[name])
    return data.getvalue()


def write_immutable(path, data):
    path = Path(path)
    if path.exists():
        assert path.is_file() and not path.is_symlink() and path.read_bytes() == data, (
            f'STOP: existing analysis output differs: {path}. Preserve and investigate; no overwrite.')
        return
    with path.open('xb') as f:
        f.write(data)


def main():
    print('===== FIX-24 / STEP 117D: LOCKED S5 PAIRED STATISTICS + SOURCE RECHECK =====', flush=True)
    checked, evidence = archive_checked_rows()
    _, summary, inference = summarize(checked)
    by_key = {(r['algorithm'], int(r['trial_index'])):r for r in checked}
    context = old_s4_context(by_key)
    assert sum(r['completed'] for r in summary) == 14982
    assert sum(r['uncompleted'] for r in summary) == 1018
    assert sum(r['energy_blocked_pair_evaluations'] for r in summary) == 148625
    assert sum(r['energy_blocked_insertions'] for r in summary) == 245775
    assert sum(r['dispatches'] for r in summary) == 10750
    assert sum(r['over_300ms'] for r in summary) == 389
    assert sum(r['s4_full_soc_completed'] for r in context) == 15962
    assert sum(r['s5_25pct_soc_completed'] for r in context) == 14982

    report = dict(
        study='FIX-24 / Step 117D S5 post-pilot-locked primary inference',
        runs_validated=160, paired_seed_sets=20,
        requested=16000, completed=14982,
        energy_blocked_candidate_evaluations=148625,
        battery_constraint_rejected_insertions=245775,
        dispatch_invocations=10750, over_300ms_dispatch_invocations=389,
        primary_endpoint='Completed requests per 100 offered per paired seed',
        primary_test='Exact two-sided paired sign-flip randomization test of seven algorithm-minus-Hungarian completion differences; Holm across all seven tests',
        ci='Unadjusted 95% paired Student-t interval (df=19), not simultaneous confidence interval',
        inference_assumption='Exchangeability of paired difference signs under the sharp null',
        context_caveat='Full-SOC S4 matched-seed comparison is contextual; kernel versions differed for timing',
        study_design_caveat='S5 condition chosen after examining exploratory pilot; protocol locked before full 160',
        physical_validation=False, hard_real_time_guarantee=False,
        evidence=evidence,
    )
    columns_summary = [
        'algorithm','runs','requested','completed','uncompleted','fulfillment_pct',
        'completed_per_seed_mean','completed_per_seed_sd',
        'gross_battery_Wh_per_run_mean','grid_import_Wh_per_run_mean',
        'gross_battery_Wh_per_completed_request',
        'energy_blocked_pair_evaluations','energy_blocked_insertions',
        'dispatches','over_300ms','over_300ms_pct',
    ]
    columns_infer = [
        'algorithm','comparator','seeds','mean_diff_completed_per_100_requests',
        'total_diff_completed_across_20_seeds','paired_t_95ci_low_unadjusted',
        'paired_t_95ci_high_unadjusted', 'pairs_better','pairs_equal','pairs_worse',
        'two_sided_exact_signflip_p','holm_p_7_fulfillment_tests','significant_at_0_05_holm',
    ]
    columns_context = [
        'algorithm','s4_full_soc_completed','s5_25pct_soc_completed',
        'paired_mean_change_in_completed_per_100',
        's4_full_soc_mean_battery_Wh_per_run','s5_25pct_soc_mean_battery_Wh_per_run',
    ]
    files = {
        'Step117D_S5_Manuscript_Results.md': article(summary, inference, context, evidence),
        'step117d_s5_algorithm_summary.csv': csv_content(columns_summary, summary),
        'step117d_s5_paired_vs_hungarian.csv': csv_content(columns_infer, inference),
        'step117d_s5_full_soc_s4_context.csv': csv_content(columns_context, context),
        'step117d_s5_inference_full.json': json_content(inference),
        'step117d_s5_integrity_audit.json': json_content(report),
        'step117b_locked_analysis_protocol.json': PROTOCOL.read_bytes(),
        'step117c_run_level_160.csv': RUN_CSV.read_bytes(),
    }
    files['README.md'] = (
        '# FIX-24 Step 117D compact S5 analysis evidence\n\n'
        'This archive summarizes all 160 S5 simulations after independent validation by the locked archived Step117C/Step117A checkers.\n'
        'It excludes the 160 full raw archives; preserve those separately.\n'
        'S5 is the S4 demand workload with initial battery SOC=25% and original fixed assumptions.\n'
        'The statistical plan was locked after exploratory pilot selection and before the full S5 campaign.\n'
        'Battery values are model predictions; full-SOC S4 timing uses a different Linux kernel.\n'
    ).encode('utf-8')

    # Recheck before creating any output. Only DEST is written; no simulation.
    assert sha(PROGRESS) == evidence['progress_sha256']
    assert sha(RUN_CSV) == evidence['run_level_csv_sha256']
    assert sha(RELEASE) == evidence['step115_evidence_zip_sha256']
    DEST.mkdir(parents=True, exist_ok=True)
    assert DEST.is_dir() and not DEST.is_symlink()
    for name, data in sorted(files.items()):
        write_immutable(DEST / name, data)
    package = make_zip(files)
    write_immutable(DEST / 'step117d_shareable_analysis.zip', package)
    print('\n===== STEP 117D S5 PAIRED ANALYSIS RESULTS =====', flush=True)
    for r in summary:
        print(f'{r["algorithm"]:14s}: {r["completed"]:4d}/2000 ({r["fulfillment_pct"]:5.2f}%), '
              f'blocked={r["energy_blocked_pair_evaluations"]:6d}, '
              f'Wh/run={r["gross_battery_Wh_per_run_mean"]:8.1f}, '
              f'deadline misses={r["over_300ms"]}/{r["dispatches"]}', flush=True)
    print('\n===== STEP 117D PRIMARY PAIRED TESTS VS HUNGARIAN =====', flush=True)
    for r in inference:
        print(f'{r["algorithm"]:14s}: mean_diff={r["mean_diff_completed_per_100_requests"]:+6.2f} '
              f'completed/100, unadjusted95CI=['
              f'{r["paired_t_95ci_low_unadjusted"]:+.2f},'
              f'{r["paired_t_95ci_high_unadjusted"]:+.2f}], '
              f'p_exact={r["two_sided_exact_signflip_p"]:.7g}, '
              f'p_Holm7={r["holm_p_7_fulfillment_tests"]:.7g}, '
              f'wins/ties/losses={r["pairs_better"]}/{r["pairs_equal"]}/{r["pairs_worse"]}', flush=True)
    print('\nPASS: All 160 S5 archived runs revalidated; 20 complete paired workloads', flush=True)
    print('PASS: Step117B locked endpoint and 7-test family respected', flush=True)
    print('NOTE: Exact sign-flip requires sign-exchangeability under the null.', flush=True)
    print('NOTE: Energy estimates physically uncalibrated; no hard real-time claim.', flush=True)
    print('NOTE: No S5 benchmark source modifications or simulation runs.', flush=True)
    print(f'Shareable ZIP: {DEST / "step117d_shareable_analysis.zip"}', flush=True)
    print('===== STEP 117D COMPLETE =====', flush=True)


if __name__ == '__main__':
    main()
