import { useMemo } from 'react';
import { ArrowRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useFetch, type DailySeries, type DemandSeries, type Kpi, type SavingsReport, type SubstationSeries } from '../api/dash';
import { useStore } from '../store';
import { useUi } from '../store/ui';
import { CityMap, type Focus } from '../map/CityMap';
import { TimeChart, type Series } from '../charts/TimeChart';
import { Columns } from '../charts/Columns';
import { Card, Empty, NetIcon, Skeleton } from '../ui';
import { CostTiles, HBars, IncidentCard, KpiStrip, Legend, NetworkHealth } from '../ui/parts';
import { go, href } from '../lib/route';
import { compact, fmt, hhmm } from '../lib/format';
import { assetBounds } from '../lib/useSensorSeries';

const ms = (iso: string) => Date.parse(iso);

export function WaterDemandCard() {
  const { data, loading, error } = useFetch<DemandSeries>('/series/demand?network=water&zoneId=all', 60000);
  const view = useMemo(() => {
    if (!data) return null;
    const actual: Series = { id: 'a', label: 'Measured', color: 'var(--water)', fill: true, points: data.actual.map((p) => ({ t: ms(p.t), v: p.v })) };
    const fc: Series = { id: 'f', label: 'Forecast', color: 'var(--water)', dash: true, width: 1.8, points: data.forecast.map((p) => ({ t: ms(p.timestamp), v: p.predicted })) };
    const band = { label: 'Forecast band', color: 'var(--water)', points: data.forecast.map((p) => ({ t: ms(p.timestamp), lo: p.lower, hi: p.upper })) };
    const peak = data.forecast.reduce((a, b) => (b.predicted > a.predicted ? b : a));
    return { series: [actual, fc], band, peak, now: ms(data.now) };
  }, [data]);
  return (
    <Card title={<span className="row" style={{ gap: 8 }}><NetIcon n="water" />Water demand, whole district</span>}
      sub="Measured, then a 24 h forecast with its band" right={data && <div style={{ textAlign: 'end' }}><div className="num" style={{ fontSize: 12.5 }}>MAPE {fmt(data.metrics.mape, 1)}%</div><div className="muted" style={{ fontSize: 11.5 }}>MAE {fmt(data.metrics.mae)} {data.unit}</div></div>}>
      {view && data ? <>
        <TimeChart label="Water demand, measured and forecast" series={view.series} band={view.band} nowT={view.now} unit={data.unit} height={230} />
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>Peak expected at <b className="num" style={{ color: 'var(--text)' }}>{hhmm(view.peak.timestamp)}</b> · <b className="num" style={{ color: 'var(--text)' }}>{fmt(view.peak.predicted)} {data.unit}</b></div>
      </> : error ? <Empty title="Could not load the demand series">{error}</Empty> : <Skeleton h={250} />}
      {loading && !data && null}
    </Card>
  );
}

export function SubstationCard() {
  const { data, error } = useFetch<SubstationSeries>('/series/substations', 30000);
  const rows = data?.lines.map((l) => {
    const v = l.points[l.points.length - 1]?.v ?? 0;
    const u = v / data.capacity;
    return { id: l.id, label: l.name, value: v, max: data.capacity, color: u > 1 ? 'var(--crit)' : u > 0.85 ? 'var(--warn)' : 'var(--elec)', note: `${fmt(u * 100)}% of ${fmt(data.capacity)}` };
  });
  const total = rows?.reduce((s, r) => s + r.value, 0) ?? 0;
  return (
    <Card title={<span className="row" style={{ gap: 8 }}><NetIcon n="electricity" />Substation load</span>} sub="Now, against firm capacity"
      right={rows && <div style={{ textAlign: 'end' }}><div className="num" style={{ fontWeight: 600 }}>{fmt(total, 1)} MW</div><div className="muted" style={{ fontSize: 11.5 }}>total</div></div>}>
      {rows ? <HBars rows={rows} unit="MW" digits={1} /> : error ? <Empty title="Could not load substations">{error}</Empty> : <Skeleton h={190} />}
      <div className="muted" style={{ fontSize: 12, marginTop: 14 }}>Bars turn amber above 85% of capacity and red above 100%.</div>
    </Card>
  );
}

export function CoolingCard() {
  const { data, error } = useFetch<DailySeries>('/series/cooling-efficiency?days=30', 300000);
  const cols = data?.days.map((d) => ({ key: d.date, label: d.date, value: d.value, color: d.value > data.target ? 'var(--serious)' : 'var(--cool)', note: `Hottest hour ${fmt(d.ambientMax, 1)} °C` }));
  const last = data?.days[data.days.length - 1];
  return (
    <Card title={<span className="row" style={{ gap: 8 }}><NetIcon n="cooling" />Cooling efficiency</span>} sub="Plant kW per ton of cooling, per day · lower is better"
      right={last && data && <div style={{ textAlign: 'end' }}><div className="num" style={{ fontWeight: 600 }}>{fmt(last.value, 2)} kW/TR</div><div className="muted" style={{ fontSize: 11.5 }}>target {fmt(data.target, 2)}</div></div>}>
      {cols && data ? <>
        <Columns label="Daily cooling plant efficiency" data={cols} height={230} unit="kW/TR" digits={2} yDomain={[0.5, 1.0]} axisDigits={2} target={{ v: data.target, label: `target ${fmt(data.target, 2)}` }} tick={(c, i) => (i === 0 || i === cols.length - 1 ? c.key.slice(5) : null)} />
        <Legend items={[{ color: 'var(--cool)', box: true, label: 'At or below target' }, { color: 'var(--serious)', box: true, label: 'Above target' }]} />
      </> : error ? <Empty title="Could not load efficiency">{error}</Empty> : <Skeleton h={250} />}
    </Card>
  );
}

function SavingsTeaser() {
  const { data } = useFetch<SavingsReport>('/savings', 60000);
  return (
    <Card title="Savings available" sub="Opportunities not yet captured"
      right={<a className="row" href={href('savings')} style={{ gap: 4, fontSize: 13 }}>All {data?.opportunities.length ?? ''}<ArrowRight size={14} /></a>}>
      {data ? <>
        <div className="row" style={{ alignItems: 'baseline', gap: 6 }}><b className="num" style={{ fontSize: 26, color: 'var(--ok-text)', fontWeight: 650 }}>{compact(data.totalMonthly)}</b><span className="muted">EGP / month</span></div>
        <div className="muted" style={{ fontSize: 12, margin: '2px 0 10px' }}>Likely range {compact(data.totalRange.p10)} to {compact(data.totalRange.p90)}</div>
        {data.opportunities.slice(0, 3).map((o) => (
          <button key={o.id} onClick={() => go('savings', o.id)} className="row spread" style={{ width: '100%', textAlign: 'start', gap: 12, padding: '9px 0', border: 0, borderTop: '1px solid var(--border)', background: 'transparent' }}>
            <span style={{ minWidth: 0 }}><span className="row" style={{ gap: 6, fontWeight: 500 }}><NetIcon n={o.network} size={14} />{o.title}</span><span className="muted" style={{ fontSize: 12 }}>{o.where}</span></span>
            <span className="num" style={{ color: 'var(--ok-text)', fontWeight: 600, whiteSpace: 'nowrap' }}>+{compact(o.monthlySaving)}</span>
          </button>
        ))}
      </> : <Skeleton h={150} />}
    </Card>
  );
}

export function Live() {
  const { t } = useTranslation();
  const kpis = useFetch<Kpi[]>('/kpis', 15000);
  const incidents = useStore((s) => s.incidents);
  const findings = useStore((s) => s.findings.data);
  const topology = useStore((s) => s.topology.data);
  const alerts = useStore((s) => s.alerts.data);
  const sel = useUi((s) => s.selectedAsset);
  const set = useUi((s) => s.set);
  const open = incidents.filter((i) => i.state !== 'resolved').sort((a, b) => b.openedAt.localeCompare(a.openedAt))[0];
  const finding = open ? findings.find((f) => f.incidentId === open.id) : undefined;

  // Fly to the incident when a new one opens.
  const focus: Focus | null = useMemo(() => {
    if (!open || !topology) return null;
    const z = topology.zones.find((x) => x.id === open.zoneIds[0]);
    const ids = alerts.filter((a) => a.incidentId === open.id).flatMap((a) => a.assetIds);
    return z ? { lng: z.center.lng, lat: z.center.lat, zoom: 14, bounds: assetBounds(topology, ids) ?? undefined, key: open.id.length } : null;
  }, [open?.id, topology]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>{t('live.title', 'New Administrative Capital')}</h1><p>{t('live.sub', 'Live twin of the water, electricity and district cooling networks.')}</p></div>
      </div>
      <KpiStrip kpis={kpis.data} loading={kpis.loading} onOpen={(k) => k.id === 'savings_available' ? go('savings') : k.id === 'cooling_efficiency' || k.id === 'water_production' || k.id === 'electric_load' ? go('forecast') : undefined} />
      <div className="live-grid">
        <CityMap height={640} selectedAsset={sel} onSelectAsset={(id) => set({ selectedAsset: id })} focus={focus} />
        <aside className="stack" style={{ gap: 16, minWidth: 0 }}>
          <IncidentCard incident={open} finding={finding} />
          <NetworkHealth />
          <SavingsTeaser />
        </aside>
      </div>
      <section className="charts-grid">
        <WaterDemandCard />
        <SubstationCard />
        <CoolingCard />
      </section>
      {finding && <span className="sr-only"><CostTiles finding={finding} /></span>}
    </div>
  );
}
