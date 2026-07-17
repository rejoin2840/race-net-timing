import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        // routed through a CSS var so the runtime type toggle (press T) can
        // swap every heading at once — see styles.css :root / [data-type]
        heading: 'var(--font-heading)',
        body: ['Space Grotesk', 'system-ui', 'sans-serif'],
      },
      colors: {
        border:  'hsl(var(--border))',
        card:    'hsl(var(--card))',
        muted:   'hsl(var(--muted))',
        'muted-fg': 'hsl(var(--muted-foreground))',
        fg:      'hsl(var(--foreground))',
        bg:      'hsl(var(--background))',
        primary: 'hsl(var(--primary))',
        accent:  'hsl(var(--accent))',
      },
    },
  },
  plugins: [],
} satisfies Config;
