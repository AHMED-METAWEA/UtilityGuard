import { Fragment, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, SlidersHorizontal } from 'lucide-react';
import { useFetch, type Opportunity, type SavingsReport } from '../api/dash';
import { useUi } from '../store/ui';
import { Card, Empty, NetIcon, NET, Skeleton } from '../ui';
import { RangeBar, Reasons } from '../ui/Calc';
import { Basis, EvidenceTag } from '../ui/Provenance';
import { HBars } from '../ui/parts';
import { compact, fmt } from '../lib/format';
import { savingRef } from '../lib/why';
import { Why } from '../ui/Why';
import type { Network } from '../api/types';

const payback = (o: Opportunity) => (o.investment === 0 ? 'Immediate' : o.paybackDays !== null && o.paybackDays < 60 ? `${fmt(o.paybackDays)} ${Math.round(o.paybackDays) === 1 ? 'day' : 'days'}` : `${fmt((o.paybackDays ?? 0) / 30, 1)} months`);

function Detail({ o }: { o: Opportunity }) {
  const set = useUi((s) => s.set);
  return (
    <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 300px), 1fr))', gap: 20, padding: '4px 4px 8px' }}>
      <div className="stack" style={{ gap: 14 }}>
        <p className="sub" style={{ margin: 0, lineHeight: 1.55 }}>{o.summary}</p>
        <div><div className="card-title" style={{ marginBottom: 8 }}>Why this is recommended</div><Reasons reasons={o.reasons} /></div>
      </div>
      <div className="stack" style={{ gap: 14 }}>
        <div style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 9, padding: '10px 12px' }}>
          <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>The formula with its values</div>
          <div className="num" style={{ fontSize: 13, overflowWrap: 'anywhere', lineHeight: 1.6 }}>{o.formula}</div>
        </div>
        <RangeBar {...o.savingRange} />
        <div>
          <div className="row spread" style={{ marginBottom: 6 }}><div className="card-title">Assumptions</div><button className="btn sm" onClick={() => set({ assumptionsOpen: true })}><SlidersHorizontal size={14} />Edit</button></div>
          <table className="table"><tbody>{o.assumptions.map((a) => <tr key={a.key}><td style={{ verticalAlign: 'top' }}><details><summary style={{ cursor: 'pointer' }}>{a.name}{a.userChanged && <span className="chip warn" style={{ marginInlineStart: 8 }}>changed</span>}</summary><div style={{ margin: '6px 0 4px' }}><Basis a={a} /></div></details></td><td className="r num" style={{ verticalAlign: 'top' }}>{fmt(a.value, a.value % 1 ? 2 : 0)} <span className="muted">{a.unit}</span></td></tr>)}</tbody></table>
        </div>
      </div>
    </div>
  );
}

export function Savings({ id }: { id?: string }) {
  const { data, error, loading } = useFetch<SavingsReport>('/savings', 30000);
  const set = useUi((s) => s.set);
  const [open, setOpen] = useState<string | null>(id ?? null);
  useEffect(() => { if (id) setOpen(id); }, [id]);

  const byNet = data && (['cooling', 'water', 'electricity'] as Network[]).map((n) => ({ n, v: data.opportunities.filter((o) => o.network === n).reduce((s, o) => s + o.monthlySaving, 0) })).filter((x) => x.v > 0);
  const maxNet = byNet ? Math.max(...byNet.map((x) => x.v)) : 1;
  const paybackDays = data && data.totalMonthly > 0 ? (data.totalInvestment / (data.totalMonthly / 30)) : 0;

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Where the district can spend less</h1><p>Each opportunity is a difference between two costs from one formula, built from measured twin data and the assumptions below. Nothing is shown without its inputs.</p></div>
        <button className="btn" onClick={() => set({ assumptionsOpen: true })}><SlidersHorizontal size={16} />Edit assumptions</button>
      </div>

      <section className="kpis">
        {data ? <>
          <div className="card kpi"><div className="kpi-label">Available per month</div><div className="kpi-value"><b style={{ color: 'var(--ok-text)' }}>{compact(data.totalMonthly)}</b><span className="kpi-unit">EGP</span></div><div className="kpi-foot">Likely {compact(data.totalRange.p10)} to {compact(data.totalRange.p90)} (P10 to P90)</div></div>
          <div className="card kpi"><div className="kpi-label">Per year</div><div className="kpi-value"><b>{compact(data.totalMonthly * 12)}</b><span className="kpi-unit">EGP</span></div><div className="kpi-foot">If every recommendation is applied</div></div>
          <div className="card kpi"><div className="kpi-label">Investment needed</div><div className="kpi-value"><b>{compact(data.totalInvestment)}</b><span className="kpi-unit">EGP, one-off</span></div><div className="kpi-foot">Across {data.opportunities.length} opportunities</div></div>
          <div className="card kpi"><div className="kpi-label">Portfolio payback</div><div className="kpi-value"><b>{fmt(paybackDays)}</b><span className="kpi-unit">days</span></div><div className="kpi-foot">Investment divided by the monthly saving</div></div>
        </> : [0, 1, 2, 3].map((i) => <div key={i} className="card kpi"><Skeleton h={14} w={120} /><Skeleton h={30} w={100} style={{ marginTop: 10 }} /><Skeleton h={12} style={{ marginTop: 14 }} /></div>)}
      </section>

      <section className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 380px), 1fr))' }}>
        <Card title="By network" sub="EGP per month available">
          {byNet ? <HBars unit="EGP" rows={byNet.map((x) => ({ id: x.n, label: NET[x.n].label, value: x.v, max: maxNet, color: NET[x.n].color }))} /> : <Skeleton h={120} />}
        </Card>
        <Card title="How sure are we" sub={data ? `${fmt(data.totalRange.runs)} simulated runs, each assumption varied within its own range` : undefined}>
          {data ? <RangeBar {...data.totalRange} /> : <Skeleton h={80} />}
          <p className="muted" style={{ fontSize: 12, margin: '10px 0 0' }}>The range is the spread of the total when prices, set-points and factors move together within their limits. Measured inputs come from the twin.</p>
        </Card>
      </section>

      <Card title="Opportunities" sub="Ranked by monthly saving. Open a row to see the reasons, the values and the assumptions." pad={false}>
        {error && !data && <Empty title="Could not load the savings advisor">{error}</Empty>}
        <div style={{ overflowX: 'auto', maxWidth: '100%' }}>
          <table className="table" style={{ minWidth: 940 }}>
            <thead><tr><th style={{ width: 28 }} /><th>Opportunity</th><th>EGP / month <EvidenceTag e="assumed" /></th><th>Likely range</th><th className="r">Investment</th><th className="r">Payback</th><th className="r">3-year value</th><th className="r">Confidence</th></tr></thead>
            <tbody>
              {!data && [0, 1, 2, 3].map((i) => <tr key={i}><td colSpan={8}><Skeleton h={34} /></td></tr>)}
              {data?.opportunities.map((o) => {
                const isOpen = open === o.id;
                return (
                  <Fragment key={o.id}>
                    <tr style={{ cursor: 'pointer', background: isOpen ? 'var(--hover)' : undefined }} onClick={() => setOpen(isOpen ? null : o.id)}>
                      <td>{isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</td>
                      <td><div className="row" style={{ gap: 8, fontWeight: 500 }}><NetIcon n={o.network} />{o.title}</div><div className="muted" style={{ fontSize: 12, marginInlineStart: 24 }}>{o.where}</div></td>
                      <td className="num" style={{ color: 'var(--ok-text)', fontWeight: 650 }}><Why refId={savingRef(o.id)}>{fmt(o.monthlySaving)}</Why></td>
                      <td className="num sub">{compact(o.savingRange.p10)} to {compact(o.savingRange.p90)}</td>
                      <td className="r num">{o.investment ? fmt(o.investment) : '0'}</td>
                      <td className="r num">{payback(o)}</td>
                      <td className="r num" title="Net present value over 36 months at the finance discount rate, after the investment">{o.npv3Years != null ? compact(o.npv3Years) : '–'}</td>
                      <td className="r num">{fmt(o.confidence * 100)}%</td>
                    </tr>
                    {isOpen && <tr><td colSpan={8} style={{ background: 'var(--surface-2)' }}><Detail o={o} /></td></tr>}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
        {loading && null}
      </Card>
    </div>
  );
}
