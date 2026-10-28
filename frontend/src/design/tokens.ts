export const colors = {
  canvas: '#080F1C', panel: '#101B2C', elevated: '#172438', border: '#27364A',
  ink: '#ECF2FA', muted: '#A1B1C7', faint: '#7589A5',
  water: '#3B9EFF', electricity: '#F5A524', cooling: '#2DD4BF',
  ok: '#42D6A4', warn: '#F5A524', fault: '#FF7185',
};
export const spacing = Object.fromEntries(Array.from({ length: 101 }, (_, i) => [i, `${i * 4}px`]));
export const radius = { none: '0', sm: '4px', DEFAULT: '8px', lg: '12px', xl: '16px', full: '9999px' };
export const typeScale = { xs: '0.6875rem', sm: '0.75rem', base: '0.8125rem', lg: '1rem', xl: '1.25rem', '2xl': '1.75rem', '3xl': '2.25rem' };

/** One source for Tailwind and runtime CSS custom properties. */
export function applyTokens() {
  const root = document.documentElement;
  Object.entries(colors).forEach(([key, value]) => root.style.setProperty(`--color-${key}`, value));
  Object.entries(spacing).forEach(([key, value]) => root.style.setProperty(`--space-${key}`, value));
  Object.entries(radius).forEach(([key, value]) => root.style.setProperty(`--radius-${key}`, value));
  Object.entries(typeScale).forEach(([key, value]) => root.style.setProperty(`--text-${key}`, value));
}
