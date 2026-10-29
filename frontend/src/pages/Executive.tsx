import { useMemo } from 'react';
import { CircleCheck, TriangleAlert } from 'lucide-react';
import { useFetch, type Kpi, type SavingsReport } from '../api/dash';
import type { AssetStatus } from '../api/types';
import { useStore } from '../store';
import { Card, Meter, NetIcon, Skeleton, StatusChip } from '../ui';
import { KpiStrip } from '../ui/parts';
import { compact, fmt } from '../lib/format';
import { go } from '../lib/route';

const RANK: Record<AssetStatus, number> = { normal: 0, warning: 1, offline: 2, fault: 3 };

export function Executive() {
  const kpis = useFetch<Kpi[]>('/kpis', 15000);
  const savings = useFetch<SavingsReport>('/savings', 30000);
  const topology = useStore((s) => s.topology.data);
  const assets = useStore((s) => s.assets);
  const incidents = useStore((s) => s.incidents);

  const zones = useMemo(() => (topology?.zones ?? []).map((z) => {
    const list = topology!.assets.filter((a) => a.zoneId === z.id);
    const worst = list.reduce<AssetStatus>((w, a) => (RANK[assets[a.id]?.status ?? 'normal'] > RANK[w] ? assets[a.id]?.status ?? 'normal' : w), 'normal');
    const health = list.length ? list.reduce((s, a) => s + (assets[a.id]?.healthScore ?? 0), 0) / list.length : 0;
    return { z, n: list.length, worst, health };
  }), [topology, assets]);
  const risky = Object.values(assets).filter((a) => a.failureProbability !== undefined).sort((a, b) => (b.failureProbability ?? 0) - (a.failureProbability ?? 0)).slice(0, 5);
  const open = incidents.filter((i) => i.state !== 'resolved').length;
  const name = (id: string) => topology?.assets.find((a) => a.id === id)?.name ?? id;

  const meets = (k: Kpi) => (k.target === undefined || k.target === null ? null : k.better === 'lower' ? k.value <= k.target : k.value >= k.target);

  return (
    <div className="page">
      <div className="page-head"><div><h1>Executive overview</h1><p>How the utilities are performing against their targets, where money can be saved, and what could fail next.</p></div></div>
      <KpiStrip kpis={kpis.data} loading={kpis.loading} />

      <section className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))' }}>
        <Card title="Scorecard" sub="Today against target" pad={false}>
          {kpis.data ? <table className="table"><thead><tr><th>Indicator</th><th className="r">Now</th><th className="r">Target</th><th>Status</th></tr></thead><tbody>
            {kpis.data.filter((k) => k.target !== undefined && k.target !== null).map((k) => { const ok = meets(k); return (
              <tr key={k.id}><td><span className="row" style={{ gap: 8 }}>{k.network && <NetIcon n={k.network} />}{k.label}</span></td><td className="r num"><b>{fmt(k.value, k.digits)}</b> <span className="muted">{k.unit}</span></td><td className="r num">{fmt(k.target, k.digits)}</td>
                <td>{ok ? <span className="chip ok"><CircleCheck size={13} />Meets target</span> : <span className="chip warn"><TriangleAlert size={13} />{k.better === 'lower' ? 'Above target' : 'Below target'}</span>}</td></tr>); })}
            <tr><td>Open incidents</td><td className="r num"><b>{open}</b></td><td className="r num">0</td><td>{open === 0 ? <span className="chip ok"><CircleCheck size={13} />Clear</span> : <span className="chip crit"><TriangleAlert size={13} />Needs attention</span>}</td></tr>
          </tbody></table> : <div className="card-body"><Skeleton h={160} /></div>}
        </Card>

        <Card title="Where money can be saved" sub="Recommended opportunities, per month"
          right={<button className="btn sm" onClick={() => go('savings')}>Open advisor</button>}>
          {savings.data ? <>
            <div className="row" style={{ alignItems: 'baseline', gap: 6 }}><b className="num" style={{ fontSize: 28, color: 'var(--ok-text)', fontWeight: 650 }}>{compact(savings.data.totalMonthly)}</b><span className="muted">EGP / month · {compact(savings.data.totalMonthly * 12)} a year</span></div>
            <div className="muted" style={{ fontSize: 12, margin: '2px 0 10px' }}>Likely range {compact(savings.data.totalRange.p10)} to {compact(savings.data.totalRange.p90)} against {compact(savings.data.totalInvestment)} EGP of investment</div>
            {savings.data.opportunities.slice(0, 4).map((o) => <button key={o.id} onClick={() => go('savings', o.id)} className="row spread" style={{ width: '100%', border: 0, borderTop: '1px solid var(--border)', background: 'transparent', padding: '9px 0', textAlign: 'start', gap: 12 }}><span className="row" style={{ gap: 8 }}><NetIcon n={o.network} size={14} />{o.title}</span><b className="num" style={{ color: 'var(--ok-text)' }}>+{compact(o.monthlySaving)}</b></button>)}
          </> : <Skeleton h={200} />}
        </Card>

        <Card title="What could fail next" sub="Highest failure risk over 30 days, from the failure model">
          {risky.length ? <div className="stack" style={{ gap: 12 }}>{risky.map((a) => <div key={a.assetId}><div className="row spread" style={{ marginBottom: 4 }}><span>{name(a.assetId)} <span className="muted" style={{ fontSize: 12 }}>health {fmt(a.healthScore)}</span></span><b className="num">{fmt((a.failureProbability ?? 0) * 100)}%</b></div><Meter value={a.failureProbability ?? 0} color={(a.failureProbability ?? 0) > 0.3 ? 'var(--warn)' : 'var(--accent)'} label={name(a.assetId)} /></div>)}</div> : <Skeleton h={180} />}
        </Card>

        <Card title="Districts" sub="Worst asset status and average health" style={{ gridColumn: '1 / -1' }}>
          <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))', gap: 12 }}>
            {zones.map(({ z, n, worst, health }) => (
              <div key={z.id} style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 12, background: worst === 'normal' ? 'var(--surface-2)' : 'var(--crit-soft)' }}>
                <div className="row spread" style={{ marginBottom: 8, gap: 8 }}><span style={{ fontWeight: 600 }}>{z.name}</span><StatusChip s={worst} /></div>
                <Meter value={health / 100} color={worst === 'normal' ? 'var(--accent)' : 'var(--crit)'} label={`${z.name} health`} />
                <div className="row spread muted" style={{ fontSize: 12, marginTop: 6 }}><span>Health <b className="num" style={{ color: 'var(--text)' }}>{fmt(health)}</b></span><span>{n} assets</span></div>
              </div>
            ))}
          </div>
        </Card>
      </section>
    </div>
  );
}
