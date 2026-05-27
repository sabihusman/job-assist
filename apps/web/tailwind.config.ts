import type { Config } from 'tailwindcss';
import animate from 'tailwindcss-animate';

/**
 * Tailwind v3 config — maps CSS-variable-backed oklch tokens (defined in
 * `src/app/globals.css`) onto named color utilities.
 *
 * The CSS variables hold just the oklch components (e.g. `60% .11 215`),
 * not the full `oklch(...)` function, so we wrap them here. That lets us
 * write `bg-primary/30` and have Tailwind compose
 * `oklch(var(--primary) / 0.3)` correctly. v4 would do this natively.
 */
const oklchVar = (name: string) => `oklch(var(--${name}) / <alpha-value>)`;

const config: Config = {
  darkMode: 'class',
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    container: {
      center: true,
      padding: '1rem',
    },
    extend: {
      colors: {
        background: oklchVar('background'),
        foreground: oklchVar('foreground'),
        surface: oklchVar('surface'),
        'surface-2': oklchVar('surface-2'),
        card: {
          DEFAULT: oklchVar('card'),
          foreground: oklchVar('card-foreground'),
        },
        muted: {
          DEFAULT: oklchVar('muted'),
          foreground: oklchVar('muted-foreground'),
        },
        border: oklchVar('border'),
        'border-strong': oklchVar('border-strong'),
        input: oklchVar('input'),
        popover: {
          DEFAULT: oklchVar('popover'),
          foreground: oklchVar('popover-foreground'),
        },
        primary: {
          DEFAULT: oklchVar('primary'),
          foreground: oklchVar('primary-foreground'),
        },
        secondary: {
          DEFAULT: oklchVar('secondary'),
          foreground: oklchVar('secondary-foreground'),
        },
        accent: {
          DEFAULT: oklchVar('accent'),
          foreground: oklchVar('accent-foreground'),
        },
        destructive: {
          DEFAULT: oklchVar('destructive'),
          foreground: oklchVar('destructive-foreground'),
        },
        positive: {
          DEFAULT: oklchVar('positive'),
          foreground: oklchVar('positive-foreground'),
        },
        negative: {
          DEFAULT: oklchVar('negative'),
          foreground: oklchVar('negative-foreground'),
        },
        pending: {
          DEFAULT: oklchVar('pending'),
          foreground: oklchVar('pending-foreground'),
        },
        ring: oklchVar('ring'),
        'tier-1': oklchVar('tier-1'),
        'tier-2': oklchVar('tier-2'),
        'tier-3': oklchVar('tier-3'),
        'tier-4': oklchVar('tier-4'),
        'ats-greenhouse': oklchVar('ats-greenhouse'),
        'ats-lever': oklchVar('ats-lever'),
        'ats-ashby': oklchVar('ats-ashby'),
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
      fontFamily: {
        sans: ['var(--font-inter)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: [
          'var(--font-jetbrains-mono)',
          'ui-monospace',
          'SFMono-Regular',
          'Menlo',
          'monospace',
        ],
      },
      /**
       * Density typography scale (UX overhaul PR 1).
       *
       * Names the pixel values that 189 arbitrary ``text-[Npx]`` call
       * sites in the codebase already use. New code should prefer the
       * named tokens; existing call sites migrate opportunistically as
       * pages get touched in later PRs. See docs/DESIGN_SYSTEM.md.
       *
       * Line-heights pinned to specific rem values rather than Tailwind's
       * default ratio multipliers — dense data UI benefits from
       * predictable vertical rhythm.
       */
      fontSize: {
        '2xs': ['0.625rem', { lineHeight: '0.875rem' }], // 10 / 14
        xs: ['0.6875rem', { lineHeight: '1rem' }], //       11 / 16
        sm: ['0.75rem', { lineHeight: '1.125rem' }], //     12 / 18
        base: ['0.8125rem', { lineHeight: '1.25rem' }], //  13 / 20
        md: ['0.875rem', { lineHeight: '1.25rem' }], //     14 / 20
        lg: ['1rem', { lineHeight: '1.5rem' }], //          16 / 24
        xl: ['1.125rem', { lineHeight: '1.625rem' }], //    18 / 26
        '2xl': ['1.5rem', { lineHeight: '2rem' }], //       24 / 32
      },
      boxShadow: {
        card: 'var(--shadow-card)',
      },
    },
  },
  plugins: [animate],
};

export default config;
