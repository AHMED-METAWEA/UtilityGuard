import { useEffect, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import { Compass, LocateFixed, Minus, Plus, WifiOff } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { FeatureCollection, Geometry as GeoGeometry } from 'geojson';
import type { Asset, Network } from '../api/types';
import { useStore } from '../store';
import { Button, Tooltip } from '../design/primitives';
import { colors } from '../design/tokens';

export default function MapView({ layers, onSelectAsset }: { layers: Record<Network, boolean>; onSelectAsset: (asset: Asset) => void }) {
  const { t } = useTranslation();
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'offline'>('loading');
  const [styleReady, setStyleReady] = useState(false);
  const topology = useStore((s) => s.topology.data);
  const zoneId = useStore((s) => s.selectedZone);
  const statusUpdates = useStore((s) => s.assets);
  const selectRef = useRef(onSelectAsset);
  selectRef.current = onSelectAsset;
  const topologyRef = useRef(topology);
  topologyRef.current = topology;

  useEffect(() => {
    if (!container.current) return;
    let instance: maplibregl.Map;
    try {
      instance = new maplibregl.Map({ container: container.current, style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json', center: [31.75, 30.02], zoom: 13, attributionControl: false, pitch: 0 });
      map.current = instance;
    } catch { setState('offline'); return; }
    const timeout = setTimeout(() => setState('offline'), 10_000);
    instance.on('load', () => { clearTimeout(timeout); setStyleReady(true); setState('ready'); });
    instance.on('error', () => setState('offline'));
    instance.on('idle', () => { if (instance.isStyleLoaded()) setState('ready'); });
    instance.on('click', (event) => {
      if (!instance.getLayer('asset-points')) return;
      const feature = instance.queryRenderedFeatures(event.point, { layers: ['asset-points', 'asset-lines'] })[0];
      const asset = topologyRef.current?.assets.find((item) => item.id === feature?.properties?.id);
      if (asset) selectRef.current(asset);
    });
    instance.on('mousemove', (event) => {
      if (!instance.getLayer('asset-points')) return;
      instance.getCanvas().style.cursor = instance.queryRenderedFeatures(event.point, { layers: ['asset-points', 'asset-lines'] }).length ? 'pointer' : '';
    });
    return () => { clearTimeout(timeout); instance.remove(); map.current = null; };
  }, []);

  useEffect(() => {
    const instance = map.current;
    if (!instance || !styleReady) return;
    const data: FeatureCollection<GeoGeometry> = { type: 'FeatureCollection', features: (topology?.assets ?? []).filter((asset) => layers[asset.network] && (!zoneId || asset.zoneId === zoneId)).flatMap((asset) => {
      if (!asset.geometry?.coordinates?.length || !['point', 'line'].includes(asset.geometry.type)) return [];
      const coordinates = asset.geometry.coordinates.map((point) => [point.lng, point.lat]);
      const geometry: GeoGeometry = asset.geometry.type === 'point' ? { type: 'Point', coordinates: coordinates[0] } : { type: 'LineString', coordinates };
      return [{ type: 'Feature' as const, geometry, properties: { id: asset.id, color: colors[asset.network], status: statusUpdates[asset.id]?.status ?? asset.status } }];
    }) };
    const source = instance.getSource('assets') as maplibregl.GeoJSONSource | undefined;
    if (source) source.setData(data);
    else {
      instance.addSource('assets', { type: 'geojson', data });
      instance.addLayer({ id: 'asset-glow', type: 'line', source: 'assets', filter: ['==', '$type', 'LineString'], paint: { 'line-color': ['get', 'color'], 'line-width': 10, 'line-blur': 8, 'line-opacity': 0.4 } });
      instance.addLayer({ id: 'asset-lines', type: 'line', source: 'assets', filter: ['==', '$type', 'LineString'], paint: { 'line-color': ['get', 'color'], 'line-width': 2 } });
      instance.addLayer({ id: 'asset-points', type: 'circle', source: 'assets', filter: ['==', '$type', 'Point'], paint: { 'circle-radius': 5, 'circle-color': ['match', ['get', 'status'], 'fault', colors.fault, 'warning', colors.warn, 'offline', colors.faint, ['get', 'color']], 'circle-stroke-width': 2, 'circle-stroke-color': colors.canvas } });
    }
  }, [layers, topology, zoneId, statusUpdates, styleReady]);

  useEffect(() => {
    const zone = topology?.zones.find((item) => item.id === zoneId);
    if (zone && map.current) map.current.flyTo({ center: [zone.center.lng, zone.center.lat], zoom: 14, duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 250 });
  }, [zoneId, topology]);

  return <div className={`map-hero map-${state}`} role="region" aria-label={t('map.label')}>
    <div ref={container} className="map-canvas" />
    <div className="map-shade" />
    <div className="map-caption"><span className="eyebrow"><span className="mini-cross" />{t('map.eyebrow')}</span><h1>{t('map.title')}<br /><span>{t('map.titleAccent')}</span></h1><p>{t('map.subtitle')}</p></div>
    <div className="map-center-reticle" aria-hidden="true"><i /><span /><i /></div>
    <div className="map-label"><span className="eyebrow">{t('map.view')}</span><span>{t('app.region')}</span></div>
    <div className="map-tools"><Tooltip content={t('map.reset')}><Button variant="icon" aria-label={t('map.reset')} onClick={() => map.current?.flyTo({ center: [31.75, 30.02], zoom: 13, bearing: 0, duration: 250 })}><LocateFixed size={17} /></Button></Tooltip><div className="zoom-tools"><Button variant="icon" aria-label={t('map.zoomIn')} onClick={() => map.current?.zoomIn({ duration: 200 })}><Plus size={17} /></Button><Button variant="icon" aria-label={t('map.zoomOut')} onClick={() => map.current?.zoomOut({ duration: 200 })}><Minus size={17} /></Button></div><Compass size={24} className="compass" aria-label={t('map.north')} /></div>
    <div className="map-footer"><span role="status">{state === 'offline' && <WifiOff size={12} />}{t(`map.${state === 'ready' ? 'live' : state === 'loading' ? 'loading' : 'offline'}`)}</span><Tooltip content={t('map.coordinatesUnit')}><span className="font-mono">{t('map.center')}</span></Tooltip></div>
    <div className="map-attribution"><a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">{t('map.source')}</a></div>
  </div>;
}
