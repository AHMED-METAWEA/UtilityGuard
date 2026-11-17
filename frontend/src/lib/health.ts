/** Where the healthcare team's own dashboard (Streamlit) runs. `VITE_HEALTH_URL` overrides it; by default it is port 8501 on the host the twin is opened from,
 * so it also works when the twin is opened from another machine. `?embed=true` hides Streamlit's own menu and footer inside the frame. */
export const HEALTH_PORT = 8501;

export function healthUrl(loc: { protocol: string; hostname: string }, override?: string): string {
  const base = override && override.trim() ? override.trim().replace(/\/+$/, '') : `${loc.protocol === 'https:' ? 'https:' : 'http:'}//${loc.hostname || 'localhost'}:${HEALTH_PORT}`;
  return base;
}

export const embedUrl = (base: string): string => `${base}/?embed=true`;
