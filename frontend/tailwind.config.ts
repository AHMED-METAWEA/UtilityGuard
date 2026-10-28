import type { Config } from 'tailwindcss';
import { colors, spacing, radius, typeScale } from './src/design/tokens';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    colors: { ...colors, transparent: 'transparent', current: 'currentColor' },
    spacing, borderRadius: radius, fontSize: typeScale,
    extend: { fontFamily: { sans: ['Inter', 'sans-serif'], mono: ['JetBrains Mono', 'monospace'] } },
  },
  plugins: [],
} satisfies Config;
