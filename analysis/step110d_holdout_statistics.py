#!/usr/bin/env python3
"""FIX-19 / STEP 110D: read-only, paired holdout inference (18 non-pilot seeds).

Reads ONLY the saved Step 110C progress report, 72-row paired table, and
selected archived checksum-protected records. Creates a separate new analysis
folder; never runs or modifies the simulator or its archives.

Statistical decisions: two-sided exact paired sign-flip tests for mean
energy difference; Holm-adjust 4 energy hypotheses; 95% paired t intervals
(descriptive, not multiplicity-adjusted). Waiting-time tests exploratory.
"""
from pathlib import Path
from itertools import product
import csv
import hashlib
import json
import math
import os
import sys
import zipfile

import numpy as np
from scipy import stats

ROOT = Path('research_revision_v2').resolve()
HOLD = ROOT / 'step110c_holdout_alpha_ablation'
PROGRESS = HOLD / 'holdout_progress.json'
PAIRED = HOLD / 'paired_alpha1_minus_alpha0_holdout.csv'
OUT = ROOT / 'step110d_holdout_inference'
ALGORITHMS = ('hungarian', 'greedy')
SCENARIOS = ('S3', 'S4')
TRIALS = tuple(range(2, 20))
SOURCE_SHA = '7b52bb550414469937da3098f91313b366137d43bd48b8465b840a97bffeb0a0'
MANIFEST_SHA = '33361ce7a27c547cfa0dbb1a1fd37506b75d263d3ccf2334805428d545096189'


def sha(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def csv_rows(path):
    with path.open(newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def record_key(r):
    return (r['scenario'], r['algorithm'], int(r['trial_index']),
            float(r['initial_soc']), float(r['alpha_energy']))


def ensure_close(x, y, label, tol=0.0001):
    assert math.isfinite(float(x)) and math.isfinite(float(y)), label
    assert abs(float(x) - float(y)) < tol, f'Mismatch {label}: {x} vs {y}'


def exact_signflip_p(values):
    """Exhaust all 2**18 sign assignments under the symmetric paired null."""
    a = np.asarray(values, dtype=np.float64)
    n = len(a)
    assert n == 18
    observed = abs(float(a.sum()))
    extreme = 0
    for start in range(0, 1 << n, 8192):
        masks = np.arange(start, min(start + 8192, 1 << n), dtype=np.uint32)
        signs = (2 * ((masks[:, None] >> np.arange(n, dtype=np.uint32)) & 1)
                 .astype(np.int8) - 1)
        signed_sums = signs @ a
        extreme += int(np.count_nonzero(np.abs(signed_sums) >= observed - 1e-8))
    return extreme / (1 << n)


def holm(pvalues):
    n = len(pvalues)
    order = sorted(range(n), key=lambda i: pvalues[i])
    adjusted = [None] * n
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, min(1.0, (n - rank) * pvalues[idx]))
        adjusted[idx] = running
    return adjusted


def ci95(values):
    a = np.asarray(values, dtype=np.float64)
    mean = float(a.mean())
    half = float(stats.t.ppf(.975, len(a)-1) * stats.sem(a))
    return mean - half, mean + half


print('===== FIX-19 / STEP 110D: INDEPENDENT HOLDOUT STATISTICS =====', flush=True)
protected = {
    ROOT / 'fix19_candidate/app.py': SOURCE_SHA,
    ROOT / 'step106b_frozen_candidate_20261029T174020Z' / 'simulator' / 'app.py': SOURCE_SHA,
    ROOT / 'step106b_frozen_candidate_20261029T174020Z' / 'campaign_640_manifest.csv': MANIFEST_SHA,
}
for p, expected in protected.items():
    assert p.is_file() and sha(p) == expected, f'Protected file mismatch: {p}'

assert PROGRESS.is_file() and PAIRED.is_file(), 'Missing Step 110C outputs'
progress = json.loads(PROGRESS.read_text(encoding='utf-8'))
assert int(progress['planned']) == 144
records = progress['records']
assert len(records) == 144
bykey = {}
for r in records:
    k = record_key(r)
    assert k not in bykey and r['status'] == 'VALIDATED', f'Duplicate/unvalidated record: {k}'
    assert k[0] in SCENARIOS and k[1] in ALGORITHMS and k[2] in TRIALS
    assert k[3] == 1.0 and k[4] in (0.0, 1.0)
    assert int(r['trial_seed']) == 1001 + 1000 * k[2] if 'trial_seed' in r else True
    a = Path(r['archive']).resolve()
    assert a.is_relative_to(HOLD) and a.is_dir(), f'Archive outside holdout: {a}'
    # Verify a selection of archived source data hashes against the Step 110C audit.
    for filename in ('run_settings.json', 'run_summary.csv', 'request_results.csv'):
        assert sha(a / filename) == r['archive_checksums'][filename], f'Archive changed: {a}/{filename}'
    bykey[k] = r

expected_keys = set(product(SCENARIOS, ALGORITHMS, TRIALS, (1.0,), (0.0,1.0)))
assert set(bykey) == expected_keys, 'Holdout cases missing/duplicated'
paired = csv_rows(PAIRED)
assert len(paired) == 72
seen = set()
for row in paired:
    s, a, seed, soc = row['scenario'], row['algorithm'], int(row['trial_seed']), float(row['initial_soc'])
    assert (seed-1001) % 1000 == 0 and seed in range(3001, 20002, 1000)
    t = (seed-1001) // 1000
    k = (s, a, t, soc)
    assert k not in seen
    seen.add(k)
    zero = bykey[(s,a,t,soc,0.0)]
    one = bykey[(s,a,t,soc,1.0)]
    assert zero['canonical_sha256'] == one['canonical_sha256'], f'Not paired: {k}'
    assert zero['initial_fleet'] == one['initial_fleet'], f'Initial fleet not paired: {k}'
    ensure_close(float(row['alpha1_minus_alpha0_battery_Wh']),
                 float(one['battery_Wh']) - float(zero['battery_Wh']), f'Energy delta {k}')
    assert int(row['alpha1_minus_alpha0_completed']) == one['completed'] - zero['completed']
    ensure_close(float(row['alpha1_minus_alpha0_capped_wait_s']),
                 float(one['capped_wait_s']) - float(zero['capped_wait_s']), f'Wait delta {k}')
    assert int(row['alpha0_completed']) == int(zero['completed'])
    assert int(row['alpha1_completed']) == int(one['completed'])

assert len(seen) == 72
print('PASS: 144 protected holdout archives and 72 paired comparisons')
print('PASS: Independent agreement between pair CSV and raw run-level records')

result = []
for s in SCENARIOS:
    for a in ALGORITHMS:
        these = sorted((r for r in paired if r['scenario'] == s and r['algorithm'] == a),
                       key=lambda r: int(r['trial_seed']))
        assert len(these) == 18
        de = np.array([float(r['alpha1_minus_alpha0_battery_Wh']) for r in these])
        dw = np.array([float(r['alpha1_minus_alpha0_capped_wait_s']) for r in these])
        dc = np.array([int(r['alpha1_minus_alpha0_completed']) for r in these])
        alpha0_mean = np.mean([float(bykey[(s,a,(int(r['trial_seed'])-1001)//1000,1.0,0.0)]['battery_Wh']) for r in these])
        r = dict(
            scenario=s, algorithm=a, paired_seeds=18,
            alpha0_mean_battery_Wh=round(float(alpha0_mean), 4),
            alpha1_mean_battery_Wh=round(float(alpha0_mean + de.mean()), 4),
            alpha1_minus_alpha0_battery_Wh=round(float(de.mean()), 4),
            battery_savings_pct=round(float(-de.mean()/alpha0_mean*100), 4),
            mean_energy_delta_ci95_low_Wh=round(ci95(de)[0], 4),
            mean_energy_delta_ci95_high_Wh=round(ci95(de)[1], 4),
            energy_signflip_two_sided_p=exact_signflip_p(de),
            energy_savings_seeds=int((de < -1e-6).sum()),
            energy_increase_seeds=int((de > 1e-6).sum()),
            mean_capped_wait_delta_s=round(float(dw.mean()), 4),
            mean_wait_delta_ci95_low_s=round(ci95(dw)[0], 4),
            mean_wait_delta_ci95_high_s=round(ci95(dw)[1], 4),
            wait_signflip_two_sided_p=exact_signflip_p(dw),
            total_completion_delta=int(dc.sum()),
        )
        result.append(r)

adjusted = holm([r['energy_signflip_two_sided_p'] for r in result])
for r, adj in zip(result, adjusted):
    r['energy_holm_adjusted_p'] = adj
    r['energy_significant_holm_0_05'] = adj < 0.05

print('\n===== ENERGY WEIGHTING: INFERENCE ON 18 HOLDOUT SEEDS =====')
print('Scenario Algorithm   Mean dWh    Savings%    95% CI dWh          exact p    Holm p   Savings seeds')
for r in result:
    print(f"{r['scenario']:8} {r['algorithm']:11} {r['alpha1_minus_alpha0_battery_Wh']:+9.2f} "
          f"{r['battery_savings_pct']:10.2f} "
          f"[{r['mean_energy_delta_ci95_low_Wh']:+8.2f},{r['mean_energy_delta_ci95_high_Wh']:+8.2f}] "
          f"{r['energy_signflip_two_sided_p']:9.5f} "
          f"{r['energy_holm_adjusted_p']:9.5f} "
          f"{r['energy_savings_seeds']:2}/18")
print('\n95% intervals are paired t intervals (unadjusted); p-values are exact sign-flip,')
print('two-sided, with Holm correction across the four energy hypotheses.')
print('Waiting time is exploratory; no hard real-time or physical energy validation.')

assert not OUT.exists(), f'Output folder already exists: {OUT}. Preserve prior analysis.'
OUT.mkdir(parents=False)
output_csv = OUT / 'step110d_paired_inference.csv'
with output_csv.open('w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=list(result[0]))
    w.writeheader()
    w.writerows(result)

md = [
    '# FIX-19 / Step 110D — Holdout inference',
    '',
    'Read-only analysis of 18 supplementary seeds (3001–20001) for each of S3/S4 × Hungarian/Greedy.',
    'No pilot seeds used. FIX-19, original 640-run campaign, and holdout archives are unchanged.',
    '',
    '## Energy effect (alpha=1 minus alpha=0)',
    '',
    '| Scenario | Algorithm | Mean difference, Wh | Reduction vs alpha=0 | Unadjusted paired 95% CI, Wh | Exact sign-flip p | Holm p |',
    '|---|---|---:|---:|---|---:|---:|',
]
for r in result:
    md.append(f"| {r['scenario']} | {r['algorithm']} | {r['alpha1_minus_alpha0_battery_Wh']:+.2f} | "
              f"{r['battery_savings_pct']:.2f}% | "
              f"[{r['mean_energy_delta_ci95_low_Wh']:+.2f}, {r['mean_energy_delta_ci95_high_Wh']:+.2f}] | "
              f"{r['energy_signflip_two_sided_p']:.5f} | {r['energy_holm_adjusted_p']:.5f} |")
md += [
    '', '## Capped waiting and completion', '',
    '| Scenario | Algorithm | Mean capped wait difference, s | 95% paired CI, s | Total completion difference |',
    '|---|---|---:|---|---:|',
]
for r in result:
    md.append(f"| {r['scenario']} | {r['algorithm']} | {r['mean_capped_wait_delta_s']:+.2f} | "
              f"[{r['mean_wait_delta_ci95_low_s']:+.2f}, {r['mean_wait_delta_ci95_high_s']:+.2f}] | "
              f"{r['total_completion_delta']:+d} |")
md += [
    '', '## Limits', '',
    '* The statistical unit is the scenario-specific seed, not individual passengers or repeated SOC variants.',
    '* Paired sign-flip tests assume symmetric/exchangeable signs under the null; intervals assume independent seed-level differences.',
    '* Only S3/S4, Hungarian/Greedy, 100% initial SOC and a single simulator calibration were tested.',
    '* This tests the explicit assignment energy-cost weight while keeping battery feasibility checks on in both arms.',
    '* This is simulated battery withdrawal, not external measured energy accuracy; grid charging is a separate outcome.',
    '* A few individual seeds increase energy under alpha=1, and waiting times do not consistently improve.',
    '* Neither the holdout nor the main campaign establishes hard real-time deadline guarantees.',
    '* The pilot informed hypothesis and experiment selection; conclusions are scoped to these conditions.',
]
report_md = OUT / 'Step110D_Holdout_Interpretation.md'
report_md.write_text('\n'.join(md)+'\n', encoding='utf-8')
with zipfile.ZipFile(OUT/'step110d_shareable_results.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for path in (output_csv, report_md, PAIRED):
        z.write(path, arcname=path.name)

for p, digest in protected.items():
    assert sha(p) == digest, f'Protected file changed: {p}'
print('\nPASS: Protected sources and original manifest unchanged')
print('CSV:', output_csv)
print('Report:', report_md)
print('Shareable ZIP:', OUT / 'step110d_shareable_results.zip')
print('===== STEP 110D COMPLETE =====')
