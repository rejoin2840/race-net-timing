import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        heading: ['Rajdhani', 'sans-serif'],
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
