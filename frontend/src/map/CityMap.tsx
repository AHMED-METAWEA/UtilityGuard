import { useEffect, useMemo, useRef, useState } from 'react';
import maplibregl, { type GeoJSONSource, type Map as MLMap, type MapLayerMouseEvent } from 'maplibre-gl';
import { renderToStaticMarkup } from 'react-dom/server';
import { Droplets, Gauge, Hospital, MapPinned, Maximize2, Minus, Plus, Snowflake, Waves, Zap } from 'lucide-react';
import type { Asset, Network } from '../api/types';
import { useStore } from '../store';
import { useTheme } from '../lib/theme';
import { NET } from '../ui';
import { useCityDistricts, useCityFacilities, type CityDistrict } from '../api/city';
import { BAND_LABEL, BAND_VAR, CITY_LAYERS, band, cityBounds, detailLines, drawnAt, facilityLine, matchNote, monthLabel, radiusFor, scoreOf, type CityLayer } from '../lib/city';

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

export interface Focus { lng: number; lat: number; zoom?: number; bounds?: [number, number, number, number]; key: number }
interface Props {
  onSelectAsset?: (id: string) => void; selectedAsset?: string | null; focus?: Focus | null;
  height?: number | string; interactive?: boolean; showLegend?: boolean; compact?: boolean;
  /** Pipes the localizer suspects, rank 1 first. Rank 1 pulses; the others get a quiet dashed outline. */
  suspects?: { assetId: string; rank: number }[];
}

export function CityMap({ onSelectAsset, selectedAsset, focus, height = 560, interactive = true, showLegend = true, compact = false, suspects }: Props) {
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

  // The healthcare team's districts and facilities (read-only), drawn beside the twin's own networks. Off until asked for, and not on small maps.
  const cityControls = interactive && !compact;
  const [cityOn, setCityOn] = useState(false);
  const [facOn, setFacOn] = useState(false);
  const [cityLayer, setCityLayer] = useState<CityLayer>('overall');
  const [month, setMonth] = useState<string | null>(null);
  const cityData = useCityDistricts(cityControls && cityOn, month).data;
  const facData = useCityFacilities(cityControls && facOn).data;
  const cityEls = useRef<Map<string, HTMLElement>>(new Map());
  const [cityTip, setCityTip] = useState<{ key: string; x: number; y: number } | null>(null);
  const [facTip, setFacTip] = useState<{ x: number; y: number; name: string; kind: string } | null>(null);

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
    cityEls.current = new Map(); // a new map starts without the old district symbols
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
      src('zones', EMPTY); src('nets', EMPTY); src('city-fac', EMPTY);
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
        if (n === 'water') {
          const none: maplibregl.FilterSpecification = ['==', ['get', 'assetId'], ''];
          add({ id: 'suspect-alt', type: 'line', source: 'nets', filter: none, layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': '#475569', 'line-width': ['interpolate', ['linear'], ['zoom'], 11, 4, 16, 11], 'line-opacity': 0.9, 'line-dasharray': [1.2, 1.2] } });
          add({ id: 'suspect-halo', type: 'line', source: 'nets', filter: none, layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': '#d03b3b', 'line-width': ['interpolate', ['linear'], ['zoom'], 11, 8, 16, 20], 'line-opacity': 0.35, 'line-blur': 3 } });
          add({ id: 'suspect-top', type: 'line', source: 'nets', filter: none, layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': '#d03b3b', 'line-width': ['interpolate', ['linear'], ['zoom'], 11, 3, 16, 7] } });
        }
        map.on('click', `net-${n}`, (e: MapLayerMouseEvent) => { const id = e.features?.[0]?.properties?.assetId; if (id) select.current?.(id); });
        map.on('mouseenter', `net-${n}`, () => { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', `net-${n}`, () => { map.getCanvas().style.cursor = ''; });
      }
      add({ id: 'city-fac', type: 'circle', source: 'city-fac', layout: { visibility: 'none' }, paint: {
        'circle-radius': ['match', ['get', 'kind'], 'hospital', 4.6, 'clinic', 3.2, 2.4], 'circle-opacity': 0.92, 'circle-color': '#000', 'circle-stroke-color': '#000', 'circle-stroke-width': 1.5 } });
      map.on('mousemove', 'city-fac', (e: MapLayerMouseEvent) => {
        const f = e.features?.[0]; if (!f) return;
        map.getCanvas().style.cursor = 'help';
        setFacTip({ x: e.point.x, y: e.point.y, name: String(f.properties?.name ?? ''), kind: String(f.properties?.kind ?? '') });
      });
      map.on('mouseleave', 'city-fac', () => { map.getCanvas().style.cursor = ''; setFacTip(null); });
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
    map.setPaintProperty('city-fac', 'circle-color', css('--text'));
    map.setPaintProperty('city-fac', 'circle-stroke-color', css('--map-bg'));
    map.setPaintProperty('suspect-alt', 'line-color', css('--text-2'));
    map.setPaintProperty('suspect-halo', 'line-color', css('--crit'));
    map.setPaintProperty('suspect-top', 'line-color', css('--crit'));
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

  // ---- suspected pipes from the leak localizer ----
  const topId = suspects?.find((x) => x.rank === 1)?.assetId ?? '';
  const altIds = (suspects ?? []).filter((x) => x.rank !== 1).map((x) => x.assetId);
  const altKey = altIds.join(',');
  useEffect(() => {
    const map = ready; if (!map) return;
    const one: maplibregl.FilterSpecification = ['==', ['get', 'assetId'], topId];
    map.setFilter('suspect-halo', one); map.setFilter('suspect-top', one);
    map.setFilter('suspect-alt', ['in', ['get', 'assetId'], ['literal', altKey ? altKey.split(',') : ['']]]);
  }, [ready, topId, altKey]);

  // ---- layer visibility ----
  useEffect(() => {
    const map = ready; if (!map) return;
    for (const n of ['water', 'electricity', 'cooling'] as Network[])
      for (const id of [`net-${n}-case`, `net-${n}`, `net-${n}-flow`]) map.setLayoutProperty(id, 'visibility', layers[n] ? 'visible' : 'none');
    for (const id of ['suspect-alt', 'suspect-halo', 'suspect-top']) map.setLayoutProperty(id, 'visibility', layers.water ? 'visible' : 'none');
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

  // ---- the healthcare team's districts: one symbol per district (their file has a centroid and an area, no boundary), coloured by their own stress score ----
  const maxResidents = useMemo(() => Math.max(0, ...(cityData?.districts.map((d) => d.metrics.residents) ?? [0])), [cityData]);
  useEffect(() => {
    const map = ready; if (!map) return;
    const have = cityEls.current;
    if (!cityData) { have.forEach((el) => { el.style.display = 'none'; }); return; }
    for (const d of cityData.districts) {
      const at = drawnAt(d);
      if (!at) { have.get(d.districtKey)?.style.setProperty('display', 'none'); continue; }
      let el = have.get(d.districtKey);
      if (!el) {
        const btn = document.createElement('button');
        btn.type = 'button'; btn.className = 'city-dot'; btn.setAttribute('aria-label', d.nameAr);
        btn.addEventListener('mouseenter', () => { const r = btn.getBoundingClientRect(), w = wrap.current!.getBoundingClientRect(); setCityTip({ key: d.districtKey, x: r.left - w.left + r.width / 2, y: r.top - w.top }); });
        btn.addEventListener('mouseleave', () => setCityTip(null));
        btn.addEventListener('focus', () => { const r = btn.getBoundingClientRect(), w = wrap.current!.getBoundingClientRect(); setCityTip({ key: d.districtKey, x: r.left - w.left + r.width / 2, y: r.top - w.top }); });
        btn.addEventListener('blur', () => setCityTip(null));
        new maplibregl.Marker({ element: btn }).setLngLat([at.lng, at.lat]).addTo(map);
        have.set(d.districtKey, btn); el = btn;
      }
      el.dataset.band = band(scoreOf(d, cityLayer));
      el.style.setProperty('--r', `${radiusFor(d.metrics.residents, maxResidents)}px`);
      el.style.display = '';
      el.textContent = d.code.replace(/^NAC-/, '');
    }
  }, [ready, cityData, cityLayer, maxResidents]);
  useEffect(() => { if (!cityOn) { cityEls.current.forEach((el) => { el.style.display = 'none'; }); setCityTip(null); } }, [cityOn]);

  useEffect(() => {
    const map = ready; if (!map) return;
    map.setLayoutProperty('city-fac', 'visibility', facOn && facData ? 'visible' : 'none');
    (map.getSource('city-fac') as GeoJSONSource | undefined)?.setData(!facData ? EMPTY : {
      type: 'FeatureCollection',
      features: facData.filter((f) => drawnAt(f)).map((f) => ({ type: 'Feature', properties: { kind: f.kind, name: f.nameAr }, geometry: { type: 'Point', coordinates: [f.placedAt!.lng, f.placedAt!.lat] } })),
    });
    if (!facOn) setFacTip(null);
  }, [ready, facOn, facData]);

  // Show both projects' places at once the first time a city layer is switched on.
  const cityShown = `${cityOn && cityData ? 'd' : ''}${facOn && facData ? 'f' : ''}`;
  useEffect(() => {
    const map = ready; if (!map || !cityShown || !bounds) return;
    const b = cityBounds(cityOn && cityData ? cityData.districts : [], facOn && facData ? facData : []); if (!b) return;
    const all = new maplibregl.LngLatBounds(bounds.getSouthWest(), bounds.getNorthEast()).extend([b[0], b[1]]).extend([b[2], b[3]]);
    map.fitBounds(all, { padding: 60, duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 900 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, cityShown]);

  // ---- fly to focus ----
  useEffect(() => {
    const map = ready; if (!map || !focus) return;
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (focus.bounds) map.fitBounds([[focus.bounds[0], focus.bounds[1]], [focus.bounds[2], focus.bounds[3]]], { padding: 70, maxZoom: 15.4, duration: reduce ? 0 : 1600, essential: true });
    else map.flyTo({ center: [focus.lng, focus.lat], zoom: focus.zoom ?? 15, duration: reduce ? 0 : 1600, curve: 1.5, essential: true });
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
      const pulse = (Math.sin(t / 380) + 1) / 2;
      map.setPaintProperty('suspect-halo', 'line-opacity', 0.15 + 0.4 * pulse);
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

  const cityDistrict: CityDistrict | undefined = cityTip ? cityData?.districts.find((d) => d.districtKey === cityTip.key) : undefined;
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
      {cityControls && <div className="map-city">
        <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
          <button className="layer-btn" aria-pressed={cityOn} onClick={() => setCityOn((v) => !v)}><MapPinned size={15} aria-hidden />Healthcare districts</button>
          <button className="layer-btn" aria-pressed={facOn} onClick={() => setFacOn((v) => !v)}><Hospital size={15} aria-hidden />Health facilities</button>
        </div>
        {cityOn && <div className="map-city-panel">
          <div className="seg" role="group" aria-label="What the districts are coloured by">
            {CITY_LAYERS.map((l) => <button key={l.id} type="button" aria-pressed={cityLayer === l.id} title={l.label} onClick={() => setCityLayer(l.id)}>{l.short}</button>)}
          </div>
          {cityData && <label className="row" style={{ gap: 6, fontSize: 12 }}><span className="muted">Month</span>
            <select value={cityData.month} aria-label="Month of the environment data" onChange={(e) => setMonth(e.target.value === cityData.months[cityData.months.length - 1] ? null : e.target.value)}>
              {[...cityData.months].reverse().map((m) => <option key={m} value={m}>{monthLabel(m)}</option>)}
            </select></label>}
        </div>}
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
        {cityControls && cityOn && <div className="map-legend-city">
          <div className="muted" style={{ fontSize: 11.5 }}>{CITY_LAYERS.find((l) => l.id === cityLayer)?.label}: stress score 0 to 100 (their definition){cityData ? `, ${monthLabel(cityData.month)}` : ''}</div>
          <div className="row" style={{ gap: 12 }}>
            {(['low', 'moderate', 'high'] as const).map((b) => <span key={b} className="row" style={{ gap: 6 }}><i className="dot" style={{ color: `var(${BAND_VAR[b]})` }} />{BAND_LABEL[b]} {b === 'low' ? '(under 20)' : b === 'moderate' ? '(20 to 35)' : '(35 and over)'}</span>)}
          </div>
          <div className="muted" style={{ fontSize: 11 }}>Circle size is registered residents. Healthcare team's data, simulated; the twin did not generate it.</div>
        </div>}
      </div>}
      {tip && tipAsset && <div className="tooltip" style={{ left: tip.x, top: tip.y - 8, transform: 'translate(-50%, -100%)', minWidth: 160 }}>
        <div className="row" style={{ gap: 6 }}><b>{tipAsset.name}</b><span className="muted">{tipAsset.kind}</span></div>
        <div className="muted">{tipState ? `${tipState.status}, health ${Math.round(tipState.healthScore)}` : 'normal'}{tipState?.failureProbability !== undefined ? `, failure risk ${Math.round(tipState.failureProbability * 100)}%` : ''}</div>
      </div>}
      {cityTip && cityDistrict && <div className="tooltip city-tip" style={{ left: cityTip.x, top: cityTip.y - 8, transform: 'translate(-50%, -100%)', minWidth: 220 }}>
        <div className="row" style={{ gap: 6 }}><b dir="auto">{cityDistrict.nameAr}</b><span className="muted">{cityDistrict.code}</span></div>
        <div className="row" style={{ gap: 6 }}><i className="dot" style={{ color: `var(${BAND_VAR[band(scoreOf(cityDistrict, cityLayer))]})` }} />
          <span>{BAND_LABEL[band(scoreOf(cityDistrict, cityLayer))]}, score <b className="num">{scoreOf(cityDistrict, cityLayer).toFixed(1)}</b></span></div>
        {detailLines(cityDistrict, cityLayer).map((l) => <div key={l.label} className="row spread" style={{ gap: 12 }}><span className="muted">{l.label}</span><span className="num">{l.value}</span></div>)}
        <div className="muted" style={{ marginTop: 4 }}>{facilityLine(cityDistrict)}</div>
        <div className="muted">{matchNote(cityDistrict)}</div>
      </div>}
      {facTip && <div className="tooltip" style={{ left: facTip.x, top: facTip.y - 10, transform: 'translate(-50%, -100%)' }}>
        <b dir="auto">{facTip.name}</b> <span className="muted">{facTip.kind}</span>
      </div>}
    </div>
  );
}
