#!/usr/bin/env python3
"""Rebuild manuscript Results figures from frozen, traceable UGV evidence.

Usage: python3 make_figures.py
Inputs are source-exact CSV/JSON members of Step 115/118 compact releases.
Figures are descriptive unless explicitly labelled with paired inferential statistics.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import MultipleLocator, FuncFormatter

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
OUT = ROOT / 'figures'
OUT.mkdir(exist_ok=True)
C = dict(dark='#263238', slate='#526777', teal='#007C83', orange='#D55E00',
         grid='#E4EAED', pale='#C8D1D6', blue='#0072B2', grey='#67757D')
NAMES = {'greedy':'Greedy', 'hungarian':'Hungarian', 'mcmf':'MCMF',
         'auction':'Auction', 'random':'Random', 'roundrobin':'Round-Robin',
         'greedy_minmax':'Greedy Min-Max', 'annealing':'Simulated Annealing'}
ALG = list(NAMES)
COLORS = {'greedy':'#0072B2','hungarian':'#222222','mcmf':'#009E73',
          'auction':'#56B4E9','random':'#D55E00','roundrobin':'#CC79A7',
          'greedy_minmax':'#E69F00','annealing':'#7A747C'}
MARKERS = dict(zip(ALG, ['o','s','D','^','v','P','X','h']))

plt.rcParams.update({
    'font.family':'DejaVu Sans', 'font.size':8.3,
    'axes.titlesize':9.5, 'axes.labelsize':8.4,
    'xtick.labelsize':7.6,'ytick.labelsize':7.9,
    'legend.fontsize':7.6, 'axes.linewidth':0.65,
    'axes.edgecolor':C['grey'],'axes.labelcolor':C['dark'],
    'xtick.color':C['dark'],'ytick.color':C['dark'],
    'text.color':C['dark'], 'figure.facecolor':'white',
    'axes.facecolor':'white','savefig.facecolor':'white',
    'pdf.fonttype':42, 'ps.fonttype':42, 'svg.fonttype':'none',
})

def load_data():
    manifest = json.loads((DATA/'source_manifest.json').read_text())
    for name,m in manifest.items():
        path=DATA/name
        if not path.is_file(): raise RuntimeError(f'Missing evidence: {path}')
        got=hashlib.sha256(path.read_bytes()).hexdigest()
        if got != m['sha256']:
            raise RuntimeError(f'EVIDENCE CHANGED: {name}; expected {m["sha256"]}, got {got}')
    def read(name): return pd.read_csv(DATA/name)
    d={
      'main': read('run_level_640.csv'),
      'pairs': read('holdout_paired.csv'),
      'ablation': read('ablation_inference.csv'),
      'physics': read('physics_sensitivity.csv'),
      'latency': read('latency_scenario.csv'),
      'audit': json.loads((DATA/'latency_audit.json').read_text()),
      's5':read('s5_algorithm_summary.csv'),
      's5context':read('s5_s4_context.csv'),
      's5paired':read('s5_paired.csv'),
    }
    main=d['main']; s5=d['s5']; ab=d['ablation']; audit=d['audit']
    assert len(main)==640 and main.completed.sum()==44762, 'Nominal-run audit mismatch'
    assert not main.duplicated(['scenario','algorithm','trial_seed']).any(), 'Duplicate primary runs'
    assert set(main.scenario)=={'S1','S2','S3','S4'} and set(main.algorithm)==set(ALG)
    assert all(n==20 for n in main.groupby(['scenario','algorithm']).size())
    assert len(d['pairs'])==72 and len(ab)==4
    assert (ab.paired_seeds==18).all()
    assert len(d['physics'])==24 and (d['physics'].seeds==2).all()
    assert len(s5)==8 and s5.completed.sum()==14982 and s5.requested.sum()==16000
    assert s5.energy_blocked_pair_evaluations.sum()==148625
    assert s5.energy_blocked_insertions.sum()==245775
    assert s5.over_300ms.sum()==389 and s5.dispatches.sum()==10750
    assert len(d['s5paired'])==7
    assert d['latency'].dispatches.sum()==25512 and d['latency'].over_300ms.sum()==77
    assert audit['dispatch_invocations']==25512 and audit['over_300ms']==77
    context=d['s5context']; assert context.s4_full_soc_completed.sum()==15962
    return d

def simple_axes(ax, axis='y'):
    ax.grid(axis=axis,color=C['grid'], linewidth=.65, zorder=0)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.tick_params(length=2.8,width=.6)

def save(fig,stem):
    fig.savefig(OUT/(stem+'.pdf'), bbox_inches='tight', pad_inches=.13)
    fig.savefig(OUT/(stem+'.svg'), bbox_inches='tight', pad_inches=.13)
    fig.savefig(OUT/(stem+'.png'), dpi=600, bbox_inches='tight', pad_inches=.13)
    plt.close(fig)
    print(f'CREATED {stem} [PDF vector, SVG vector, PNG 600 dpi]')

def fig01_nominal(d):
    a=d['main'].groupby(['scenario','algorithm'], as_index=False).agg(
        energy=('battery_Wh','mean'),wait=('mean_pickup_response_picked_up_s','mean'),
        completed=('completed','sum'), requests=('requests','sum'))
    fig, axs=plt.subplots(2,2,figsize=(7.2,5.05),sharex=True,sharey=True)
    for ax,s in zip(axs.flat,['S1','S2','S3','S4']):
        sub=a[a.scenario==s].set_index('algorithm')
        ref=sub.loc['hungarian']
        for key in ALG:
            row=sub.loc[key]
            x=row.wait-ref.wait; y=row.energy/ref.energy
            ax.scatter(x,y,color=COLORS[key],marker=MARKERS[key],s=35,
                       edgecolors='white',linewidths=.55,zorder=3)
        ax.axvline(0,color=C['grey'],lw=.8,ls='--',alpha=.7,zorder=1)
        ax.axhline(1,color=C['grey'],lw=.8,ls='--',alpha=.7,zorder=1)
        simple_axes(ax,'both')
        ax.set_xlim(-88,43); ax.set_ylim(.91,2.08)
        vehicles=int(d['main'].loc[d['main'].scenario==s,'vehicles'].iloc[0])
        req=int(d['main'].loc[d['main'].scenario==s,'requests'].iloc[0])
        ax.set_title(f'{s} · {vehicles} UGVs / {req} requests', loc='left',fontweight='bold')
        ax.yaxis.set_major_locator(MultipleLocator(.25))
        ax.xaxis.set_major_locator(MultipleLocator(30))
    for ax in axs[:,0]: ax.set_ylabel('Gross energy / Hungarian (ratio)')
    for ax in axs[-1,:]: ax.set_xlabel('Mean picked-up waiting time − Hungarian (s)')
    handles=[Line2D([0],[0],marker=MARKERS[k],color='none',markerfacecolor=COLORS[k],
             markeredgecolor='white',label=NAMES[k],markersize=7) for k in ALG]
    fig.legend(handles=handles,loc='lower center',bbox_to_anchor=(.5,-.02),
               ncol=4,frameon=False,columnspacing=1.2,handletextpad=.38)
    fig.subplots_adjust(left=.105,right=.99,top=.96,bottom=.18,hspace=.23,wspace=.2)
    save(fig,'Fig01_Nominal_Energy_Wait_Tradeoff')

def fig02_ablation(d):
    a=d['ablation'].copy()
    a['label']=a.apply(lambda r:f'{r.scenario} · {NAMES[r.algorithm]}',axis=1)
    fig,(ax,bx)=plt.subplots(1,2,figsize=(7.2,3.18),sharey=True,
                           gridspec_kw={'width_ratios':[1.3,.95]})
    y=np.arange(4)[::-1]
    for i,(_,r) in enumerate(a.iterrows()):
        yy=y[i]
        sig=bool(r.energy_significant_holm_0_05)
        col=C['teal'] if sig else C['slate']
        lo=float(r.mean_energy_delta_ci95_low_Wh);hi=float(r.mean_energy_delta_ci95_high_Wh)
        mean=float(r.alpha1_minus_alpha0_battery_Wh)
        ax.plot([lo,hi],[yy,yy],lw=1.6,color=col,zorder=2)
        ax.plot([lo,lo],[yy-.08,yy+.08],lw=1.15,color=col)
        ax.plot([hi,hi],[yy-.08,yy+.08],lw=1.15,color=col)
        ax.scatter([mean],[yy],s=43,marker='o',facecolor=col if sig else 'white',
                   edgecolor=col,linewidth=1.3,zorder=3)
        lo2=float(r.mean_wait_delta_ci95_low_s);hi2=float(r.mean_wait_delta_ci95_high_s)
        mean2=float(r.mean_capped_wait_delta_s)
        bx.plot([lo2,hi2],[yy,yy],lw=1.6,color=C['slate'],zorder=2)
        bx.plot([lo2,lo2],[yy-.08,yy+.08],color=C['slate'],lw=1.1)
        bx.plot([hi2,hi2],[yy-.08,yy+.08],color=C['slate'],lw=1.1)
        bx.scatter([mean2],[yy],s=39,marker='o',facecolor='white',
                   edgecolor=C['slate'],linewidth=1.3,zorder=3)
    for q in (ax,bx):
        q.axvline(0,color=C['dark'],lw=.9,ls='--',zorder=1)
        q.set_ylim(-.6,3.6);simple_axes(q,'x')
        q.set_yticks(y)
    ax.set_yticklabels(a.label.tolist());bx.tick_params(axis='y',left=False,labelleft=False)
    ax.set_xlim(-260,40);bx.set_xlim(-12,15)
    ax.set_title('(a) Modeled battery-energy effect',loc='left',fontweight='bold')
    bx.set_title('(b) Deadline-limited waiting effect',loc='left',fontweight='bold')
    ax.set_xlabel('α=1 minus α=0 (Wh per run)')
    bx.set_xlabel('α=1 minus α=0 (s)')
    fig.text(.52,.025,'Intervals: unadjusted paired 95% t-CIs · Filled points: Holm-significant energy effects',
             ha='center',fontsize=7,color=C['slate'])
    fig.subplots_adjust(left=.19,right=.98,bottom=.23,top=.85,wspace=.27)
    save(fig,'Fig02_Energy_Ablation_Paired_Effects')

def fig03_s4_s5(d):
    a=d['s5context'].set_index('algorithm')
    order=['mcmf','greedy','annealing','auction','hungarian','greedy_minmax','random','roundrobin']
    fig,ax=plt.subplots(figsize=(7.2,3.85))
    for i,k in enumerate(order):
        yy=len(order)-i-1
        s4=float(a.loc[k,'s4_full_soc_completed'])/20
        s5=float(a.loc[k,'s5_25pct_soc_completed'])/20
        ax.plot([s5,s4],[yy,yy],color=C['pale'],lw=2.2,zorder=1)
        ax.scatter([s4],[yy],marker='o',s=52,facecolor='white',edgecolor=C['dark'],linewidth=1.35,zorder=3)
        ax.scatter([s5],[yy],marker='o',s=52,facecolor=C['teal'],edgecolor='white',linewidth=.6,zorder=4)
        ax.text(102.4,yy,f'{s5-s4:+.2f}',ha='center',va='center',fontsize=8,
                color=C['orange'] if s5-s4 < -10 else C['dark'])
    ax.set_xlim(75,105.5);ax.set_ylim(-.75,7.75)
    ax.set_yticks(np.arange(8)[::-1], [NAMES[k] for k in order])
    ax.set_xticks([75,80,85,90,95,100]);ax.set_xlabel('Passenger-request fulfillment (%)')
    simple_axes(ax,'x')
    ax.text(102.4,7.65,'Δ (pp)',ha='center',va='bottom',fontsize=8,fontweight='bold')
    handles=[Line2D([0],[0],marker='o',linestyle='None',markerfacecolor='white',
                markeredgecolor=C['dark'],label='S4 · initial SOC 100%',markersize=7),
             Line2D([0],[0],marker='o',linestyle='None',markerfacecolor=C['teal'],
                markeredgecolor='white',label='S5 · initial SOC 25%',markersize=7)]
    fig.legend(handles=handles,loc='lower center',bbox_to_anchor=(.55,-.02),
               ncol=2,frameon=False)
    fig.subplots_adjust(left=.235,right=.98,bottom=.18,top=.97)
    save(fig,'Fig03_S4_S5_Fulfillment_Change')

def fig04_s5_inference(d):
    a=d['s5paired'].set_index('algorithm')
    order=['greedy','mcmf','auction','random','roundrobin','greedy_minmax','annealing']
    zoom=['greedy','mcmf','auction','greedy_minmax','annealing']
    fig,(ax,bx)=plt.subplots(1,2,figsize=(7.2,4.25),
                gridspec_kw={'width_ratios':[1.16,1.0]})
    def panel(ax,keys,xlim,subtitle):
        for i,k in enumerate(keys):
            r=a.loc[k]; y=len(keys)-i-1
            mean=float(r.mean_diff_completed_per_100_requests)
            lo=float(r.paired_t_95ci_low_unadjusted)
            hi=float(r.paired_t_95ci_high_unadjusted)
            sig=bool(r.significant_at_0_05_holm)
            color=C['orange'] if sig else C['slate']
            ax.plot([lo,hi],[y,y],color=color,lw=1.8,zorder=2)
            ax.plot([lo,lo],[y-.09,y+.09],color=color,lw=1.1)
            ax.plot([hi,hi],[y-.09,y+.09],color=color,lw=1.1)
            ax.scatter([mean],[y],s=42,marker='s' if sig else 'o',
                       facecolor=color if sig else 'white',edgecolor=color,
                       linewidth=1.1,zorder=3)
        ax.axvline(0,color=C['dark'],lw=.85,ls='--',zorder=1)
        ax.set_xlim(*xlim);ax.set_ylim(-.5,len(keys)-.5)
        ax.set_yticks(np.arange(len(keys))[::-1],[NAMES[k] for k in keys])
        ax.set_title(subtitle,loc='left',fontweight='bold');simple_axes(ax,'x')
    panel(ax,order,(-23,2),'(a) All seven comparisons')
    panel(bx,zoom,(-4.2,1.75),'(b) Detail near zero')
    ax.set_xlabel('Mean difference vs Hungarian (pp)')
    bx.set_xlabel('Mean difference vs Hungarian (pp)')
    fig.text(.53,.027,'Paired 95% t-CIs are unadjusted; filled squares denote Holm-adjusted p < 0.05',
             ha='center',fontsize=7.1,color=C['slate'])
    fig.subplots_adjust(left=.235,right=.98,bottom=.17,top=.90,wspace=.33)
    save(fig,'Fig04_S5_Fulfillment_Inference')

def fig05_dispatch(d):
    a=d['latency']; s=d['s5'].set_index('algorithm')
    fig,(ax,bx)=plt.subplots(1,2,figsize=(7.2,3.95),
                      gridspec_kw={'width_ratios':[1.12,1.05]})
    x=np.arange(4)
    for col,label,color,mark in [('p50_ms','Median',C['blue'],'o'),
                                 ('p95_ms','p95',C['teal'],'D'),
                                 ('p99_ms','p99',C['orange'],'s')]:
        ax.plot(x,a[col],lw=1.55,marker=mark,markersize=5.4,color=color,label=label,zorder=3)
    ax.axhline(300,lw=1,ls='--',color=C['dark'],zorder=1)
    ax.text(2.95,303,'300 ms budget',ha='right',va='bottom',fontsize=7,color=C['dark'])
    ax.set_xticks(x,a.scenario);ax.set_ylim(0,330)
    ax.set_ylabel('Instrumented dispatch latency (ms)');simple_axes(ax,'y')
    ax.set_title('(a) Primary S1–S4 dispatch percentiles',loc='left',fontweight='bold')
    ax.legend(loc='upper left',frameon=False,ncol=3,handlelength=1.4,columnspacing=.8)
    order=['greedy','hungarian','mcmf','auction','random','roundrobin','greedy_minmax','annealing']
    ys=np.arange(len(order))[::-1]
    vals=[float(s.loc[k,'over_300ms_pct']) for k in order]
    bars=bx.barh(ys,vals,height=.58,color=[C['orange'] if v>=5 else C['teal'] for v in vals],zorder=3)
    bx.set_yticks(ys,[NAMES[k] for k in order]);bx.set_xlim(0,14.2)
    bx.set_xlabel('Dispatches exceeding 300 ms (%)');simple_axes(bx,'x')
    bx.set_title('(b) S5 algorithm-level exceedances',loc='left',fontweight='bold')
    for y,v in zip(ys,vals): bx.text(v+.23,y,f'{v:.2f}%',fontsize=7.2,va='center')
    fig.text(.51,.02,'Descriptive latency data; S5 and S1–S4 ran under different kernel revisions',
             ha='center',fontsize=7.15,color=C['slate'])
    fig.subplots_adjust(left=.09,right=.98,bottom=.21,top=.88,wspace=.44)
    save(fig,'Fig05_Dispatch_Latency_and_Budget')

def figS01_physics(d):
    a=d['physics'].copy()
    a['pct']=100*a.mean_delta_battery_Wh/a.baseline_mean_battery_Wh
    order=['rolling_low','rolling_high','efficiency_low','efficiency_high','auxiliary_high','mass_high']
    label={'rolling_low':'Rolling resistance 0.008',
           'rolling_high':'Rolling resistance 0.020',
           'efficiency_low':'Drivetrain efficiency 0.80',
           'efficiency_high':'Drivetrain efficiency 0.95',
           'auxiliary_high':'Auxiliary load 250 W',
           'mass_high':'Unladen mass 1,025 kg'}
    groups=[('S3','hungarian'),('S3','greedy'),('S4','hungarian'),('S4','greedy')]
    shifts=[-.21,-.07,.07,.21]
    marks=['o','s','D','^']
    colors=[C['teal'],C['blue'],C['orange'],C['slate']]
    fig,ax=plt.subplots(figsize=(7.2,3.75))
    for j,(sc,al) in enumerate(groups):
        g=a[(a.scenario==sc)&(a.algorithm==al)].set_index('variant')
        xs=[float(g.loc[v,'pct']) for v in order]
        ys=[5-i+shifts[j] for i in range(len(order))]
        ax.scatter(xs,ys,marker=marks[j],color=colors[j],s=42,zorder=3,
                   edgecolors='white',linewidth=.5,label=f'{sc} · {NAMES[al]}')
    ax.axvline(0,color=C['dark'],lw=.9,ls='--')
    ax.set_yticks(np.arange(6)[::-1], [label[k] for k in order])
    ax.set_xlim(-26,49);ax.set_ylim(-.65,5.65)
    ax.set_xlabel('Change in modeled gross battery-energy withdrawal (%)')
    simple_axes(ax,'x')
    handles,labels=ax.get_legend_handles_labels()
    fig.legend(handles,labels,loc='upper center',bbox_to_anchor=(.59,.99),
               ncol=4,frameon=False,columnspacing=.7,handletextpad=.2)
    fig.text(.51,.015,'Exploratory one-factor variations; two matched seeds/group; points are NOT confidence intervals',
             ha='center',fontsize=7.2,color=C['slate'])
    fig.subplots_adjust(left=.31,right=.99,bottom=.18,top=.81)
    save(fig,'FigS01_Exploratory_Physics_Sensitivity')

def figS02_phase(d):
    data=d['audit']['phase_summary']
    df=pd.DataFrame(data)
    groups=[('ALL DISPATCHES','All dispatches\n(n = 25,512)'),
            ('MISSED DISPATCHES','Budget exceedances\n(n = 77)')]
    phases=['matrix_ms','solver_ms','cardinality_diagnostic_ms','activation_ms','other_dispatch_ms']
    names=['Cost-matrix construction','Solver','Cardinality diagnostics','Assignment activation','Other']
    cols=[C['teal'],C['blue'],C['orange'],C['grey'],C['pale']]
    fig,ax=plt.subplots(figsize=(7.2,2.6))
    for y,(scope,lab) in enumerate(groups[::-1]):
        rows=df[df.scope==scope].set_index('phase')
        left=0
        for p,col in zip(phases,cols):
            val=float(rows.loc[p,'share_of_total_dispatch_ms'])
            ax.barh(y,val,left=left,color=col,height=.5,edgecolor='white',lw=.4,zorder=3)
            if p=='matrix_ms':ax.text(val/2,y,f'{val:.2f}%',ha='center',va='center',
                                      fontsize=9,color='white',fontweight='bold')
            left+=val
    ax.set_yticks([0,1],['Budget exceedances\n(n = 77)','All dispatches\n(n = 25,512)'])
    ax.set_xlim(0,100);ax.set_xlabel('Share of summed dispatch-processing wall time (%)')
    simple_axes(ax,'x')
    handles=[plt.Rectangle((0,0),1,1,color=c,label=n) for c,n in zip(cols,names)]
    fig.legend(handles=handles,loc='lower center',bbox_to_anchor=(.53,-.035),
               ncol=3,frameon=False,columnspacing=.65,handlelength=1)
    fig.subplots_adjust(left=.27,right=.98,bottom=.34,top=.96)
    save(fig,'FigS02_Dispatch_Phase_Attribution')

def main():
    d=load_data(); print('SHA256 evidence checks and arithmetic checks: PASS')
    fig01_nominal(d);fig02_ablation(d);fig03_s4_s5(d);fig04_s5_inference(d)
    fig05_dispatch(d);figS01_physics(d);figS02_phase(d)
    for p in sorted(OUT.glob('*.pdf')):
        assert p.stat().st_size>1000, f'Empty PDF {p}'
    print('DONE: 7 figures × (PDF, SVG, 600-dpi PNG) = 21 files')

if __name__=='__main__':main()
