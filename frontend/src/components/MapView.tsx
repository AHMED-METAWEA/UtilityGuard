import { useCallback, useEffect, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import { Compass, LocateFixed, Minus, Plus, WifiOff } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { FeatureCollection, Geometry as GeoGeometry } from 'geojson';
import type { Asset, Network, Position } from '../api/types';
import { useStore } from '../store';
import { Button, Tooltip } from '../design/primitives';
import { colors } from '../design/tokens';
import { isOpenAlert } from '../presentation';

const zoneColors = { government: '#ac8bbd', business: '#b9a16a', residential: '#609a9f', services: '#6f8eae' };
const symbols = { pump: 'P', reservoir: 'R', pipe: '≋', substation: 'ϟ', feeder: 'ϟ', transformer: 'T', plant: '❄', loop: '↻' };
const statusSymbols = { normal: '●', warning: '▲', fault: '◆', offline: '×' };
const empty: FeatureCollection = { type: 'FeatureCollection', features: [] };
const pointAlong = (coordinates: Position[], progress: number): [number, number] => {
  const lengths = coordinates.slice(1).map((p, i) => Math.hypot(p.lng - coordinates[i].lng, p.lat - coordinates[i].lat));
  let distance = lengths.reduce((sum, length) => sum + length, 0) * progress;
  for (let i = 0; i < lengths.length; i++) {
    if (distance <= lengths[i]) { const ratio = lengths[i] ? distance / lengths[i] : 0; return [coordinates[i].lng + (coordinates[i + 1].lng - coordinates[i].lng) * ratio, coordinates[i].lat + (coordinates[i + 1].lat - coordinates[i].lat) * ratio]; }
    distance -= lengths[i];
  }
  return [coordinates[0].lng, coordinates[0].lat];
};

export default function MapView({ layers, onSelectAsset }: { layers: Record<Network, boolean>; onSelectAsset: (asset: Asset) => void }) {
  const { t } = useTranslation();
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'offline'>('loading');
  const [styleReady, setStyleReady] = useState(false);
  const topology = useStore((s) => s.topology.data);
  const zoneId = useStore((s) => s.selectedZone);
  const statusUpdates = useStore((s) => s.assets);
  const alerts = useStore((s) => s.alerts.data);
  const incidents = useStore((s) => s.incidents);
  const selectRef = useRef(onSelectAsset); selectRef.current = onSelectAsset;
  const topologyRef = useRef(topology); topologyRef.current = topology;
  const fit = useCallback(() => {
    const instance = map.current;
    const data = topologyRef.current;
    if (!instance || !data) return;
    const points = [...data.zones.flatMap((z) => z.polygon), ...data.assets.flatMap((a) => a.geometry.coordinates)];
    if (!points.length) return;
    const bounds = new maplibregl.LngLatBounds(); points.forEach((p) => bounds.extend([p.lng, p.lat]));
    const width = instance.getContainer().clientWidth;
    instance.fitBounds(bounds, { padding: width > 900 ? { left: width > 1279 ? 290 : 250, right: width > 1279 ? 440 : 370, top: 60, bottom: 260 } : 48, duration: 0, maxZoom: 15 });
  }, []);

  useEffect(() => {
    if (!container.current) return;
    let instance: maplibregl.Map;
    try {
      // Entirely local style: topology remains visible without internet or tile services.
      instance = new maplibregl.Map({ container: container.current, style: { version: 8, sources: {}, layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#0c1726' } }] }, center: [31.75, 30.02], zoom: 13, attributionControl: false, pitch: 0 });
      map.current = instance;
    } catch { setState('offline'); return; }
    instance.on('load', () => { setStyleReady(true); setState('ready'); });
    instance.on('error', () => setState('offline'));
    const hitLayers = () => ['sensors', 'asset-lines'].filter((id) => instance.getLayer(id));
    instance.on('click', (event) => {
      const ids = hitLayers(); if (!ids.length) return;
      const feature = instance.queryRenderedFeatures([[event.point.x - 5, event.point.y - 5], [event.point.x + 5, event.point.y + 5]], { layers: ids })[0];
      const asset = topologyRef.current?.assets.find((item) => item.id === feature?.properties?.assetId);
      if (asset) selectRef.current(asset);
    });
    instance.on('mousemove', (event) => { const ids = hitLayers(); instance.getCanvas().style.cursor = ids.length && instance.queryRenderedFeatures(event.point, { layers: ids }).length ? 'pointer' : ''; });
    const observer = new ResizeObserver(() => { instance.resize(); }); observer.observe(container.current);
    return () => { observer.disconnect(); instance.remove(); map.current = null; };
  }, []);

  useEffect(() => { if (styleReady && topology) fit(); }, [styleReady, topology, fit]);

  useEffect(() => {
    const instance = map.current;
    if (!instance || !styleReady || !topology) return;
    const activeZones = new Set([...alerts.filter(isOpenAlert).map((a) => a.zoneId), ...incidents.filter((i) => i.state !== 'resolved' && !alerts.some((a) => a.incidentId === i.id)).flatMap((i) => i.zoneIds)]);
    const zones: FeatureCollection<GeoGeometry> = { type: 'FeatureCollection', features: topology.zones.filter((z) => !zoneId || zoneId === z.id).map((zone) => {
      const ring = zone.polygon.map((p) => [p.lng, p.lat]);
      if (ring.length) ring.push(ring[0]);
      return { type: 'Feature', geometry: { type: 'Polygon', coordinates: [ring] }, properties: { color: zoneColors[zone.type], active: activeZones.has(zone.id) } };
    }) };
    const update = (id: string, data: FeatureCollection) => { const source = instance.getSource(id) as maplibregl.GeoJSONSource | undefined; if (source) source.setData(data); else instance.addSource(id, { type: 'geojson', data }); };
    update('zones', zones);
    if (!instance.getLayer('zone-fill')) {
      instance.addLayer({ id: 'zone-fill', type: 'fill', source: 'zones', paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.1 } });
      instance.addLayer({ id: 'zone-border', type: 'line', source: 'zones', paint: { 'line-color': ['case', ['get', 'active'], colors.fault, ['get', 'color']], 'line-width': 1, 'line-opacity': 0.6 } });
      instance.addLayer({ id: 'zone-incident', type: 'fill', source: 'zones', filter: ['==', 'active', true], paint: { 'fill-color': colors.fault, 'fill-opacity': 0.08 } });
    }
    const visibleAssets = topology.assets.filter((a) => layers[a.network] && (!zoneId || a.zoneId === zoneId));
    const assets: FeatureCollection<GeoGeometry> = { type: 'FeatureCollection', features: visibleAssets.filter((a) => a.geometry.type === 'line').map((asset) => {
      const status = statusUpdates[asset.id]?.status ?? asset.status;
      return { type: 'Feature', geometry: { type: 'LineString', coordinates: asset.geometry.coordinates.map((p) => [p.lng, p.lat]) }, properties: { assetId: asset.id, color: status === 'fault' ? colors.fault : status === 'warning' ? colors.warn : colors[asset.network], offline: status === 'offline' } };
    }) };
    update('assets', assets);
    if (!instance.getLayer('asset-lines')) {
      instance.addLayer({ id: 'asset-glow', type: 'line', source: 'assets', paint: { 'line-color': ['get', 'color'], 'line-width': 8, 'line-blur': 7, 'line-opacity': ['case', ['get', 'offline'], 0.05, 0.4] } });
      instance.addLayer({ id: 'asset-lines', type: 'line', source: 'assets', paint: { 'line-color': ['get', 'color'], 'line-width': 2, 'line-opacity': ['case', ['get', 'offline'], 0.2, 0.85] } });
    }
    const sensors: FeatureCollection<GeoGeometry> = { type: 'FeatureCollection', features: topology.sensors.filter((s) => layers[s.network] && visibleAssets.some((a) => a.id === s.assetId)).map((sensor) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [sensor.position.lng, sensor.position.lat] }, properties: { assetId: sensor.assetId, status: statusUpdates[sensor.assetId]?.status ?? topology.assets.find((a) => a.id === sensor.assetId)?.status } })) };
    update('sensors', sensors);
    if (!instance.getLayer('sensors')) instance.addLayer({ id: 'sensors', type: 'circle', source: 'sensors', paint: { 'circle-radius': 3.5, 'circle-color': ['match', ['get', 'status'], 'fault', colors.fault, 'warning', colors.warn, 'offline', colors.faint, '#43d6a4'], 'circle-opacity': ['case', ['==', ['get', 'status'], 'offline'], 0.35, 1], 'circle-stroke-width': 1.5, 'circle-stroke-color': colors.canvas } });
    const markers: maplibregl.Marker[] = [];
    for (const zone of topology.zones.filter((z) => !zoneId || zoneId === z.id)) {
      const element = document.createElement('div'); element.className = `zone-map-label ${activeZones.has(zone.id) ? 'zone-active' : ''}`; element.textContent = zone.name;
      const north = Math.max(...zone.polygon.map((p) => p.lat));
      markers.push(new maplibregl.Marker({ element, anchor: 'top' }).setLngLat([zone.center.lng, north - 0.0007]).addTo(instance));
    }
    for (const asset of visibleAssets) {
      if (!asset.geometry.coordinates.length) continue;
      const status = statusUpdates[asset.id]?.status ?? asset.status;
      const button = document.createElement('button'); button.className = `asset-map-marker marker-${status} network-${asset.network}`; button.type = 'button'; button.title = `${asset.name} · ${t(`key.${status}`)}`; button.setAttribute('aria-label', button.title);
      const icon = document.createElement('span'); icon.textContent = status === 'normal' ? symbols[asset.kind] : statusSymbols[status]; button.appendChild(icon);
      button.addEventListener('click', (event) => { event.stopPropagation(); selectRef.current(asset); });
      markers.push(new maplibregl.Marker({ element: button }).setLngLat(pointAlong(asset.geometry.coordinates, 0.5)).addTo(instance));
    }
    return () => markers.forEach((marker) => marker.remove());
  }, [layers, topology, zoneId, statusUpdates, alerts, incidents, styleReady, t]);

  useEffect(() => {
    const instance = map.current;
    if (!instance || !styleReady || !topology) return;
    if (!instance.getSource('flow')) { instance.addSource('flow', { type: 'geojson', data: empty }); instance.addLayer({ id: 'flow', type: 'circle', source: 'flow', paint: { 'circle-radius': 2, 'circle-color': ['get', 'color'], 'circle-opacity': 0.85 } }); }
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const paths = topology.assets.filter((asset) => asset.geometry.type === 'line' && ['water', 'cooling'].includes(asset.network) && layers[asset.network] && (!zoneId || zoneId === asset.zoneId));
    const phases = new Map(paths.map((a) => [a.id, 0]));
    let frame = 0; let last = performance.now();
    const animate = (now: number) => {
      frame = requestAnimationFrame(animate);
      if (now - last < 65) return;
      const elapsed = Math.min(now - last, 200) / 1000; last = now;
      const state = useStore.getState();
      const offline = (id: string) => (state.assets[id]?.status ?? topology.assets.find((a) => a.id === id)?.status) === 'offline';
      const features: FeatureCollection['features'] = [];
      for (const asset of paths) {
        const drivers = topology.links.filter((l) => l.toAsset === asset.id && ['supplies', 'cools'].includes(l.relation)).map((l) => l.fromAsset);
        // A pipe also supplies its local pump; both directions are present in the fixture topology.
        drivers.push(...topology.links.filter((l) => l.fromAsset === asset.id && l.relation === 'supplies').map((l) => l.toAsset));
        if (offline(asset.id) || drivers.some(offline)) continue;
        const sensor = topology.sensors.find((s) => s.assetId === asset.id && s.kind === 'flow');
        const flow = sensor ? state.readings[sensor.id]?.at(-1) : undefined;
        const driverSensor = !sensor ? topology.sensors.find((s) => drivers.includes(s.assetId) && s.kind === 'flow') : undefined;
        const reading = flow ?? (driverSensor ? state.readings[driverSensor.id]?.at(-1) : undefined);
        if (reading?.quality === 'missing' || (reading && reading.value <= 0)) continue;
        const speed = reading ? Math.abs(reading.value) / 18000 : 0.035;
        const phase = ((phases.get(asset.id) ?? 0) + speed * elapsed) % 1; phases.set(asset.id, phase);
        for (let i = 0; i < 3; i++) features.push({ type: 'Feature', geometry: { type: 'Point', coordinates: pointAlong(asset.geometry.coordinates, (phase + i / 3) % 1) }, properties: { color: colors[asset.network] } });
      }
      (instance.getSource('flow') as maplibregl.GeoJSONSource)?.setData({ type: 'FeatureCollection', features });
      if (instance.getLayer('zone-incident')) instance.setPaintProperty('zone-incident', 'fill-opacity', 0.065 + (Math.sin(now / 650) + 1) * 0.025);
    };
    frame = requestAnimationFrame(animate);
    return () => { cancelAnimationFrame(frame); (instance.getSource('flow') as maplibregl.GeoJSONSource | undefined)?.setData(empty); };
  }, [styleReady, topology, layers, zoneId]);

  useEffect(() => {
    const zone = topology?.zones.find((item) => item.id === zoneId);
    if (zone && map.current && styleReady) map.current.flyTo({ center: [zone.center.lng, zone.center.lat], zoom: 14, duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 250 });
    else if (styleReady) fit();
  }, [zoneId, topology, styleReady, fit]);

  return <div className={`map-hero map-${state} ${topology ? 'has-topology' : ''}`} role="region" aria-label={t('map.label')}>
    <div ref={container} className="map-canvas" /><div className="map-shade" />
    {!topology && <div className="map-caption"><span className="eyebrow">{t('map.eyebrow')}</span><h1>{t('map.title')}<br /><span>{t('map.titleAccent')}</span></h1><p>{t('map.subtitle')}</p></div>}
    <div className="map-tools"><Tooltip content={t('map.reset')}><Button variant="icon" aria-label={t('map.reset')} onClick={fit}><LocateFixed size={17} /></Button></Tooltip><div className="zoom-tools"><Button variant="icon" aria-label={t('map.zoomIn')} onClick={() => map.current?.zoomIn({ duration: 200 })}><Plus size={17} /></Button><Button variant="icon" aria-label={t('map.zoomOut')} onClick={() => map.current?.zoomOut({ duration: 200 })}><Minus size={17} /></Button></div><Compass size={24} className="compass" aria-label={t('map.north')} /></div>
    <div className="map-footer"><span role="status">{state === 'offline' && <WifiOff size={12} />}{t(`map.${state === 'ready' ? 'live' : state === 'loading' ? 'loading' : 'offline'}`)}</span><span>{t('map.topologyView')}</span></div>
  </div>;
}
