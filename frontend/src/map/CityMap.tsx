import { useEffect, useMemo, useRef, useState } from 'react';
import maplibregl, { type GeoJSONSource, type Map as MLMap, type MapLayerMouseEvent } from 'maplibre-gl';
import { renderToStaticMarkup } from 'react-dom/server';
import { Droplets, Gauge, Maximize2, Minus, Plus, Snowflake, Waves, Zap } from 'lucide-react';
import type { Asset, Network } from '../api/types';
import { useStore } from '../store';
import { useTheme } from '../lib/theme';
import { NET } from '../ui';

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const EMPTY = { type: 'FeatureCollection', features: [] } as GeoJSON.FeatureCollection;

const LANDMARKS: { name: string; lng: number; lat: number }[] = [
  { name: 'Iconic Tower', lng: 31.6938, lat: 30.0128 },
  { name: 'Parliament', lng: 31.7589, lat: 30.0136 },
  { name: 'Cabinet', lng: 31.7511, lat: 30.0135 },
  { name: 'Presidential Palace', lng: 31.7554, lat: 30.0306 },
  { name: 'Green River', lng: 31.6600, lat: 30.0080 },
];
const KIND_ICON: Record<string, React.ReactElement> = {
  pump: <Gauge size={13} strokeWidth={2.4} />, reservoir: <Waves size={13} strokeWidth={2.4} />,
  substation: <Zap size={13} strokeWidth={2.4} />, plant: <Snowflake size={13} strokeWidth={2.4} />,
};
const ZONE_SHORT: Record<string, string> = {
  'zone-gov': 'Government District', 'zone-cult': 'Arts and Culture City', 'zone-cbd': 'CBD', 'zone-res-2': 'R2', 'zone-just': 'Justice City',
  'zone-pres': 'Presidential', 'zone-res-n': 'Residential North', 'zone-res-ne': 'Residential NE', 'zone-res-far': 'Residential Far NE',
};
const MARKER_KINDS = new Set(Object.keys(KIND_ICON));

export interface Focus { lng: number; lat: number; zoom?: number; key: number }
interface Props {
  onSelectAsset?: (id: string) => void; selectedAsset?: string | null; focus?: Focus | null;
  height?: number | string; interactive?: boolean; showLegend?: boolean; compact?: boolean;
}

export function CityMap({ onSelectAsset, selectedAsset, focus, height = 560, interactive = true, showLegend = true, compact = false }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const markers = useRef<Map<string, { el: HTMLElement; marker: maplibregl.Marker; asset: Asset }>>(new Map());
  const zoneLabels = useRef<Map<string, HTMLElement>>(new Map());
  const landmarkEls = useRef<HTMLElement[]>([]);
  const [ready, setReady] = useState<MLMap | null>(null);
  const [layers, setLayers] = useState<Record<Network, boolean>>({ water: true, electricity: true, cooling: true });
  const [tip, setTip] = useState<{ id: string; x: number; y: number } | null>(null);
  const { theme } = useTheme();

  const topology = useStore((s) => s.topology.data);
  const assets = useStore((s) => s.assets);
  const alerts = useStore((s) => s.alerts.data);
  const select = useRef(onSelectAsset); select.current = onSelectAsset;

  const bounds = useMemo(() => {
    if (!topology) return null;
    const pts = topology.zones.flatMap((z) => z.polygon);
    return new maplibregl.LngLatBounds([Math.min(...pts.map((p) => p.lng)), Math.min(...pts.map((p) => p.lat))], [Math.max(...pts.map((p) => p.lng)), Math.max(...pts.map((p) => p.lat))]);
  }, [topology]);

  const hasTopology = !!topology;
  const boundsKey = bounds?.toString() ?? '';
  const openZones = useMemo(() => new Set(alerts.filter((a) => a.state !== 'resolved' && a.severity === 'critical').map((a) => a.zoneId)), [alerts]);
  const statusOf = (id: string) => assets[id]?.status ?? 'normal';

  // ---- create the map once the topology is known ----
  useEffect(() => {
    if (!host.current || !topology || !bounds || mapRef.current) return;
    const map = new maplibregl.Map({
      container: host.current, attributionControl: false, interactive, bounds, fitBoundsOptions: { padding: compact ? 24 : 48 }, minZoom: 11, maxZoom: 18,
      style: { version: 8, sources: {}, layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#0b1322' } }] },
      dragRotate: false, pitchWithRotate: false, touchPitch: false,
    });
    map.touchZoomRotate.disableRotation();
    map.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: 'Map data © OpenStreetMap contributors (ODbL)' }), 'bottom-right');
    mapRef.current = map;
    if (import.meta.env.DEV) (window as unknown as { __map: MLMap }).__map = map;
    let alive = true;

    map.on('load', () => {
      if (!alive) return;
      const src = (id: string, data: string | GeoJSON.FeatureCollection) => map.addSource(id, { type: 'geojson', data });
      src('parks', '/geo/parks.geojson'); src('water', '/geo/water.geojson'); src('river', '/geo/green-river.geojson');
      src('roads', '/geo/roads.geojson'); src('buildings', '/geo/buildings.geojson');
      src('zones', EMPTY); src('nets', EMPTY);
      const add = (l: maplibregl.LayerSpecification) => map.addLayer(l);
      add({ id: 'parks', type: 'fill', source: 'parks', paint: { 'fill-color': '#000', 'fill-opacity': 0.9 } });
      add({ id: 'water', type: 'fill', source: 'water', paint: { 'fill-color': '#000' } });
      add({ id: 'river', type: 'fill', source: 'river', paint: { 'fill-color': '#000' } });
      add({ id: 'road-minor', type: 'line', source: 'roads', filter: ['in', ['get', 'cls'], ['literal', ['secondary', 'tertiary']]], layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': '#000', 'line-width': ['interpolate', ['linear'], ['zoom'], 11, 0.5, 15, 2.5, 18, 8] } });
      add({ id: 'road-major', type: 'line', source: 'roads', filter: ['in', ['get', 'cls'], ['literal', ['motorway', 'trunk', 'primary']]], layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': '#000', 'line-width': ['interpolate', ['linear'], ['zoom'], 11, 1, 15, 4.5, 18, 14] } });
      add({ id: 'bldg', type: 'fill', source: 'buildings', paint: { 'fill-color': ['match', ['get', 'k'], 'gov', '#000', 'cbd', '#000', '#000'], 'fill-opacity': ['interpolate', ['linear'], ['zoom'], 11, 0.55, 14, 0.95] } });
      add({ id: 'zone-fill', type: 'fill', source: 'zones', paint: { 'fill-color': '#000', 'fill-opacity': 1 } });
      add({ id: 'zone-alert', type: 'fill', source: 'zones', filter: ['==', ['get', 'alert'], 1], paint: { 'fill-color': '#d03b3b', 'fill-opacity': 0.14 } });
      add({ id: 'zone-line', type: 'line', source: 'zones', paint: { 'line-color': '#000', 'line-width': 1.2, 'line-dasharray': [3, 2] } });
      add({ id: 'zone-line-alert', type: 'line', source: 'zones', filter: ['==', ['get', 'alert'], 1], paint: { 'line-color': '#d03b3b', 'line-width': 2 } });
      for (const n of ['water', 'electricity', 'cooling'] as Network[]) {
        const f: maplibregl.FilterSpecification = ['==', ['get', 'network'], n];
        const color: maplibregl.ExpressionSpecification = ['case', ['==', ['get', 'status'], 'fault'], '#d03b3b', ['==', ['get', 'status'], 'warning'], '#fab219', ['==', ['get', 'status'], 'offline'], '#7f8ea8', '#000'];
        add({ id: `net-${n}-case`, type: 'line', source: 'nets', filter: f, layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': '#000', 'line-width': ['interpolate', ['linear'], ['zoom'], 11, 3, 16, 9], 'line-opacity': 0.9 } });
        add({ id: `net-${n}`, type: 'line', source: 'nets', filter: f, layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': color, 'line-width': ['interpolate', ['linear'], ['zoom'], 11, 1.6, 16, 4.5] } });
        add({ id: `net-${n}-flow`, type: 'line', source: 'nets', filter: ['all', f, ['==', ['get', 'status'], 'normal']], layout: { 'line-cap': 'butt' }, paint: { 'line-color': '#ffffff', 'line-opacity': 0.75, 'line-width': ['interpolate', ['linear'], ['zoom'], 11, 1, 16, 2], 'line-dasharray': [0, 4, 3] } });
        map.on('click', `net-${n}`, (e: MapLayerMouseEvent) => { const id = e.features?.[0]?.properties?.assetId; if (id) select.current?.(id); });
        map.on('mouseenter', `net-${n}`, () => { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', `net-${n}`, () => { map.getCanvas().style.cursor = ''; });
      }
      setReady(map);
    });
    return () => { alive = false; map.remove(); mapRef.current = null; markers.current.clear(); zoneLabels.current.clear(); landmarkEls.current = []; setReady(null); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasTopology, boundsKey, interactive, compact]);

  // ---- theme colours ----
  useEffect(() => {
    const map = ready; if (!map) return;
    map.setPaintProperty('bg', 'background-color', css('--map-bg'));
    map.setPaintProperty('parks', 'fill-color', css('--map-park'));
    map.setPaintProperty('water', 'fill-color', css('--map-water'));
    map.setPaintProperty('river', 'fill-color', css('--map-river'));
    map.setPaintProperty('road-minor', 'line-color', css('--map-road'));
    map.setPaintProperty('road-major', 'line-color', css('--map-road-major'));
    map.setPaintProperty('bldg', 'fill-color', ['match', ['get', 'k'], 'gov', css('--map-bldg-gov'), 'cbd', css('--map-bldg-cbd'), css('--map-bldg')]);
    map.setPaintProperty('zone-fill', 'fill-color', css('--zone-fill'));
    map.setPaintProperty('zone-line', 'line-color', css('--zone-line'));
    for (const n of ['water', 'electricity', 'cooling'] as Network[]) {
      const c = css(`--${n === 'electricity' ? 'elec' : n === 'cooling' ? 'cool' : 'water'}`);
      map.setPaintProperty(`net-${n}-case`, 'line-color', css('--map-bg'));
      map.setPaintProperty(`net-${n}`, 'line-color', ['case', ['==', ['get', 'status'], 'fault'], css('--crit'), ['==', ['get', 'status'], 'warning'], css('--warn'), ['==', ['get', 'status'], 'offline'], css('--text-3'), c]);
    }
  }, [theme, ready]);

  // ---- zones + network data ----
  useEffect(() => {
    const map = ready; if (!map || !topology) return;
    (map.getSource('zones') as GeoJSONSource | undefined)?.setData({
      type: 'FeatureCollection',
      features: topology.zones.map((z) => ({ type: 'Feature', properties: { id: z.id, alert: openZones.has(z.id) ? 1 : 0 }, geometry: { type: 'Polygon', coordinates: [z.polygon.map((p) => [p.lng, p.lat])] } })),
    });
  }, [ready, topology, openZones]);

  useEffect(() => {
    const map = ready; if (!map || !topology) return;
    (map.getSource('nets') as GeoJSONSource | undefined)?.setData({
      type: 'FeatureCollection',
      features: topology.assets.filter((a) => a.geometry.type === 'line').map((a) => ({
        type: 'Feature', properties: { assetId: a.id, network: a.network, status: statusOf(a.id) },
        geometry: { type: 'LineString', coordinates: a.geometry.coordinates.map((p) => [p.lng, p.lat]) },
      })),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, topology, assets]);

  // ---- layer visibility ----
  useEffect(() => {
    const map = ready; if (!map) return;
    for (const n of ['water', 'electricity', 'cooling'] as Network[])
      for (const id of [`net-${n}-case`, `net-${n}`, `net-${n}-flow`]) map.setLayoutProperty(id, 'visibility', layers[n] ? 'visible' : 'none');
    markers.current.forEach(({ el, asset }) => { el.style.display = layers[asset.network] ? '' : 'none'; });
  }, [layers, ready]);

  // ---- DOM markers: assets, zone names, landmarks ----
  useEffect(() => {
    const map = ready; if (!map || !topology || markers.current.size) return;
    for (const a of topology.assets) {
      if (!MARKER_KINDS.has(a.kind) || a.geometry.type !== 'point') continue;
      const el = document.createElement('button');
      el.className = 'asset'; el.type = 'button'; el.setAttribute('aria-label', a.name);
      el.style.setProperty('--c', NET[a.network].color);
      el.innerHTML = renderToStaticMarkup(KIND_ICON[a.kind]);
      el.addEventListener('click', (e) => { e.stopPropagation(); select.current?.(a.id); });
      el.addEventListener('mouseenter', () => { const r = el.getBoundingClientRect(), w = wrap.current!.getBoundingClientRect(); setTip({ id: a.id, x: r.left - w.left + r.width / 2, y: r.top - w.top }); });
      el.addEventListener('mouseleave', () => setTip(null));
      const c = a.geometry.coordinates[0];
      markers.current.set(a.id, { el, marker: new maplibregl.Marker({ element: el }).setLngLat([c.lng, c.lat]).addTo(map), asset: a });
    }
    for (const z of topology.zones) {
      const el = document.createElement('div'); el.className = 'zone-label'; el.textContent = ZONE_SHORT[z.id] ?? z.name;
      zoneLabels.current.set(z.id, el);
      const lng = z.polygon.map((p) => p.lng), lat = z.polygon.map((p) => p.lat);
      new maplibregl.Marker({ element: el, anchor: 'bottom', offset: [0, -3] }).setLngLat([(Math.min(...lng) + Math.max(...lng)) / 2, Math.max(...lat)]).addTo(map);
    }
    if (!compact) for (const l of LANDMARKS) {
      const el = document.createElement('div'); el.className = 'landmark'; el.textContent = l.name;
      landmarkEls.current.push(el);
      new maplibregl.Marker({ element: el, anchor: 'left', offset: [8, 0] }).setLngLat([l.lng, l.lat]).addTo(map);
    }
    const showLandmarks = () => { const on = map.getZoom() >= 13.4; landmarkEls.current.forEach((e) => { e.style.opacity = on ? '1' : '0'; }); };
    map.on('zoom', showLandmarks); showLandmarks();
  }, [ready, topology, compact]);

  useEffect(() => { markers.current.forEach(({ el }, id) => { el.dataset.status = statusOf(id); el.classList.toggle('sel', id === selectedAsset); }); // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assets, selectedAsset, ready]);
  useEffect(() => { zoneLabels.current.forEach((el, id) => el.classList.toggle('alert', openZones.has(id))); }, [openZones, ready]);

  // ---- fly to focus ----
  useEffect(() => {
    const map = ready; if (!map || !focus) return;
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    map.flyTo({ center: [focus.lng, focus.lat], zoom: focus.zoom ?? 15, duration: reduce ? 0 : 1600, curve: 1.5, essential: true });
  }, [focus, ready]);

  // ---- animated flow along healthy lines ----
  useEffect(() => {
    const map = ready; if (!map || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const seq = [[0, 4, 3], [0.5, 4, 2.5], [1, 4, 2], [1.5, 4, 1.5], [2, 4, 1], [2.5, 4, 0.5], [3, 4, 0], [0, 0.5, 3, 3.5], [0, 1, 3, 3], [0, 1.5, 3, 2.5], [0, 2, 3, 2], [0, 2.5, 3, 1.5], [0, 3, 3, 1], [0, 3.5, 3, 0.5]];
    let step = 0, last = 0, raf = 0;
    const tick = (t: number) => {
      raf = requestAnimationFrame(tick);
      if (t - last < 70 || document.hidden) return;
      last = t; step = (step + 1) % seq.length;
      for (const n of ['water', 'electricity', 'cooling'] as Network[]) if (layers[n]) map.setPaintProperty(`net-${n}-flow`, 'line-dasharray', seq[step]);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [ready, layers]);

  // ---- resize ----
  useEffect(() => {
    if (!wrap.current) return;
    const ro = new ResizeObserver(() => mapRef.current?.resize());
    ro.observe(wrap.current); return () => ro.disconnect();
  }, []);

  const tipAsset = tip ? markers.current.get(tip.id)?.asset : null;
  const tipState = tip ? assets[tip.id] : null;
  return (
    <div className="map-wrap" ref={wrap} style={{ height }}>
      <div ref={host} style={{ position: 'absolute', inset: 0 }} />
      {!topology && <div className="empty" style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center' }}><div><b>Loading the district</b>Waiting for the twin.</div></div>}
      {interactive && <div className="map-layers">
        {(['water', 'electricity', 'cooling'] as Network[]).map((n) => { const I = NET[n].icon; return (
          <button key={n} className="layer-btn" aria-pressed={layers[n]} onClick={() => setLayers((l) => ({ ...l, [n]: !l[n] }))}>
            <I size={15} color={NET[n].color} strokeWidth={2.4} aria-hidden />{n === 'water' ? 'Water' : n === 'electricity' ? 'Electricity' : 'Cooling'}
          </button>); })}
      </div>}
      {interactive && <div className="map-tools">
        <button className="btn icon sm" aria-label="Zoom in" onClick={() => mapRef.current?.zoomIn()}><Plus size={16} /></button>
        <button className="btn icon sm" aria-label="Zoom out" onClick={() => mapRef.current?.zoomOut()}><Minus size={16} /></button>
        <button className="btn icon sm" aria-label="Fit to district" onClick={() => bounds && mapRef.current?.fitBounds(bounds, { padding: 48, duration: 900 })}><Maximize2 size={15} /></button>
      </div>}
      {showLegend && !compact && <div className="map-legend" aria-label="Map key">
        <div className="row" style={{ gap: 12 }}>
          <span className="row" style={{ gap: 6 }}><Droplets size={13} color="var(--water)" />Water</span>
          <span className="row" style={{ gap: 6 }}><Zap size={13} color="var(--elec)" />Power</span>
          <span className="row" style={{ gap: 6 }}><Snowflake size={13} color="var(--cool)" />Cooling</span>
        </div>
        <div className="row" style={{ gap: 12 }}>
          <span className="row" style={{ gap: 6 }}><i className="dot" style={{ color: 'var(--text-3)' }} />Normal</span>
          <span className="row" style={{ gap: 6 }}><i className="dot" style={{ color: 'var(--warn)' }} />Warning</span>
          <span className="row" style={{ gap: 6 }}><i className="dot" style={{ color: 'var(--crit)' }} />Fault</span>
        </div>
      </div>}
      {tip && tipAsset && <div className="tooltip" style={{ left: tip.x, top: tip.y - 8, transform: 'translate(-50%, -100%)', minWidth: 160 }}>
        <div className="row" style={{ gap: 6 }}><b>{tipAsset.name}</b><span className="muted">{tipAsset.kind}</span></div>
        <div className="muted">{tipState ? `${tipState.status}, health ${Math.round(tipState.healthScore)}` : 'normal'}{tipState?.failureProbability !== undefined ? `, failure risk ${Math.round(tipState.failureProbability * 100)}%` : ''}</div>
      </div>}
    </div>
  );
}
