import type { ReactNode } from 'react';
import { ArrowRight, CircleCheck, ShieldCheck } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { AgentFinding, Incident, Network } from '../api/types';
import type { Kpi as KpiData } from '../api/dash';
import { useStore } from '../store';
import { href } from '../lib/route';
import { ago, compact, egp, fmt } from '../lib/format';
import { EvidenceTag } from './Provenance';
import { Card, Kpi, Meter, NET, NetIcon, SeverityChip, type KpiProps } from '.';

const NET_COLOR: Record<string, string> = { water: 'var(--water)', electricity: 'var(--elec)', cooling: 'var(--cool)' };

/** Map a backend KPI to a tile: colours, delta wording and direction. Nothing is computed here beyond formatting. */
export function kpiProps(k: KpiData, onClick?: () => void): KpiProps {
  const color = k.network ? NET_COLOR[k.network] : 'var(--accent)';
  const value = k.id === 'savings_available' ? compact(k.value) : fmt(k.value, k.digits);
  let delta: KpiProps['delta'];
  if (k.delta !== undefined && k.delta !== null) {
    const dir = k.delta > 0 ? 'up' : k.delta < 0 ? 'down' : 'flat';
    const good = dir === 'flat' || !k.better ? null : (k.better === 'lower') === (dir === 'down');
    delta = { text: `${k.delta > 0 ? '+' : ''}${fmt(k.delta, k.digits)}`, good, dir };
  }
  const total = k.id === 'districts_served' ? Number(k.unit.replace(/\D/g, '')) : 0;
  const viz = total ? (
    <div role="img" aria-label={`${k.value} of ${total} districts fully served`} style={{ display: 'flex', gap: 4, height: 36, alignItems: 'center' }}>
      {Array.from({ length: total }, (_, i) => <span key={i} style={{ flex: 1, height: 14, borderRadius: 4, background: i < k.value ? 'var(--ok)' : 'var(--crit)', opacity: i < k.value ? 0.75 : 1 }} />)}
    </div>
  ) : undefined;
  return {
    viz, label: k.label, value, unit: k.unit, color, spark: k.series.length ? k.series.map((p) => p.v) : undefined, target: k.target,
    foot: k.note, delta, onClick, evidence: k.evidence, icon: k.network ? <NetIcon n={k.network} size={14} /> : undefined,
  };
}

export const KpiStrip = ({ kpis, loading, onOpen }: { kpis: KpiData[] | null; loading: boolean; onOpen?: (k: KpiData) => void }) => (
  <section className="kpis" aria-label="Key indicators">
    {loading && !kpis ? Array.from({ length: 6 }, (_, i) => <Kpi key={i} label="" value="" color="var(--accent)" loading />)
      : kpis?.map((k) => <Kpi key={k.id} {...kpiProps(k, onOpen ? () => onOpen(k) : undefined)} />)}
  </section>
);

export function HBars({ rows, unit, digits = 0 }: { rows: { id: string; label: string; value: number; max: number; color: string; note?: string }[]; unit: string; digits?: number }) {
  return (
    <div className="stack" style={{ gap: 14 }}>
      {rows.map((r) => (
        <div key={r.id}>
          <div className="row spread" style={{ marginBottom: 5 }}>
            <span style={{ fontWeight: 500 }}>{r.label}</span>
            <span className="num"><b>{fmt(r.value, digits)}</b> <span className="muted">{unit}{r.note ? ` · ${r.note}` : ''}</span></span>
          </div>
          <Meter value={r.value / r.max} color={r.color} label={`${r.label} ${fmt(r.value, digits)} ${unit}`} />
        </div>
      ))}
    </div>
  );
}

/** Cost tiles: act now, wait N hours, saving. Values straight from the backend cost object. */
export function CostTiles({ finding }: { finding: AgentFinding }) {
  const c = finding.cost;
  const tile = (label: string, value: number, sub: string, good = false) => (
    <div style={{ background: good ? 'var(--ok-soft)' : 'var(--surface-2)', border: good ? '1px solid color-mix(in srgb, var(--ok) 40%, transparent)' : '1px solid var(--border)', borderRadius: 10, padding: '10px 12px', minWidth: 0 }}>
      <div style={{ fontSize: 12, color: good ? 'var(--ok-text)' : 'var(--text-2)' }}>{label}</div>
      <div className="num" style={{ fontSize: 20, fontWeight: 650, color: good ? 'var(--ok-text)' : 'var(--text)', margin: '2px 0' }}>{fmt(value)}</div>
      <div style={{ fontSize: 11.5, color: good ? 'var(--ok-text)' : 'var(--text-3)' }}>{sub}</div>
    </div>
  );
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8 }}>
      {tile('Act now', c.actNow, c.actNowLosses ? `EGP · repair + ${fmt(c.responseHours ?? 1)} h of losses` : 'EGP')}
      {tile(`Wait ${fmt(c.delayHours)} h`, c.costOfDelay, 'EGP · losses + late repair')}
      {tile('Saving', c.expectedSaving, 'EGP if you act now', true)}
      <div className="row" style={{ gridColumn: '1 / -1', gap: 6, fontSize: 11.5, color: 'var(--text-3)' }}><EvidenceTag e="calculated" />from the cost model's formulas<EvidenceTag e="assumed" />and the prices in the assumptions</div>
    </div>
  );
}

export function IncidentCard({ incident, finding }: { incident?: Incident; finding?: AgentFinding }) {
  const { t } = useTranslation();
  const topology = useStore((s) => s.topology.data);
  const alerts = useStore((s) => s.alerts.data);
  if (!incident) {
    return (
      <Card className="ok-card">
        <div className="row" style={{ gap: 12 }}>
          <span style={{ width: 36, height: 36, borderRadius: 10, display: 'grid', placeItems: 'center', background: 'var(--ok-soft)' }}><ShieldCheck size={20} color="var(--ok-text)" /></span>
          <div><div className="card-title">{t('live.allclear', 'All systems normal')}</div><div className="card-sub">{t('live.allclear2', 'No open incidents. Run a scenario to see the agents respond.')}</div></div>
        </div>
      </Card>
    );
  }
  const zones = incident.zoneIds.map((z) => topology?.zones.find((x) => x.id === z)?.name ?? z).join(', ');
  const alert = alerts.find((a) => a.incidentId === incident.id);
  return (
    <Card style={{ borderColor: incident.severity === 'critical' ? 'color-mix(in srgb, var(--crit) 55%, var(--border))' : undefined }}>
      <div className="row spread" style={{ marginBottom: 8 }}>
        <span className="row" style={{ gap: 8 }}><SeverityChip s={incident.severity} /><span className="muted num">{incident.id}</span></span>
        {alert && <span className="muted" style={{ fontSize: 12 }}>{ago(alert.detectedAt)} ago</span>}
      </div>
      <div style={{ fontSize: 17, fontWeight: 650, letterSpacing: '-.01em' }}>{incident.title}</div>
      <div className="sub" style={{ margin: '4px 0 12px' }}>{incident.reanalysing ? 'The incident grew. The agents are re-analysing it, and the figures below are from the earlier stage.' : finding ? finding.headline : `${zones}. The agents are analysing.`}</div>
      {finding ? <div style={incident.reanalysing ? { opacity: 0.45 } : undefined}><CostTiles finding={finding} /></div> : <div className="skeleton" style={{ height: 76 }} />}
      <a className="row" href={href('incidents', incident.id)} style={{ gap: 6, marginTop: 12, fontWeight: 600 }}>{t('live.open', 'Open incident')}<ArrowRight size={15} /></a>
    </Card>
  );
}

export function NetworkHealth() {
  const topology = useStore((s) => s.topology.data);
  const assets = useStore((s) => s.assets);
  const rows = (['water', 'electricity', 'cooling'] as Network[]).map((n) => {
    const list = topology?.assets.filter((a) => a.network === n) ?? [];
    const st = list.map((a) => assets[a.id]).filter(Boolean);
    const health = st.length ? st.reduce((s, x) => s + x.healthScore, 0) / st.length : 0;
    const fault = st.filter((x) => x.status === 'fault' || x.status === 'offline').length;
    const warn = st.filter((x) => x.status === 'warning').length;
    return { n, count: list.length, health, fault, warn };
  });
  return (
    <Card title="Network health" sub={topology ? `${topology.assets.length} assets · ${topology.sensors.length} sensors` : undefined}>
      <div className="stack" style={{ gap: 14 }}>
        {rows.map((r) => (
          <div key={r.n}>
            <div className="row spread" style={{ marginBottom: 5 }}>
              <span className="row" style={{ gap: 8, fontWeight: 500 }}><NetIcon n={r.n} />{NET[r.n].label}</span>
              <span className="row" style={{ gap: 8 }}>
                <span className="num sub">{fmt(r.health)}</span>
                {r.fault ? <span className="chip crit">{r.fault} fault</span> : r.warn ? <span className="chip warn">{r.warn} warning</span> : <span className="chip ok"><CircleCheck size={13} />Normal</span>}
              </span>
            </div>
            <Meter value={r.health / 100} color={NET[r.n].color} label={`${NET[r.n].label} health`} />
          </div>
        ))}
      </div>
    </Card>
  );
}

export const Legend = ({ items }: { items: { color: string; label: ReactNode; dash?: boolean; box?: boolean }[] }) => (
  <div className="legend">{items.map((i, k) => <span key={k}><i className={`swatch ${i.box ? 'box' : ''}`} style={{ background: i.dash ? `repeating-linear-gradient(90deg, ${i.color} 0 4px, transparent 4px 7px)` : i.color }} />{i.label}</span>)}</div>
);

export { egp };
