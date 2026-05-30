import type { Metadata, Viewport } from 'next';
import { Inter, JetBrains_Mono } from 'next/font/google';
import { Toaster } from 'sonner';

import { ServiceWorkerRegistrar } from '@/components/chrome/ServiceWorkerRegistrar';
import { QueryProvider } from '@/lib/api/query-provider';
import { ThemeProvider } from '@/lib/theme/provider';

import './globals.css';

/**
 * Variable fonts loaded via `next/font/google`. The CSS variable names
 * (`--font-inter`, `--font-jetbrains-mono`) line up with
 * `tailwind.config.ts → fontFamily.{sans,mono}`.
 */
const inter = Inter({
  subsets: ['latin'],
  variable: '--font-inter',
  display: 'swap',
});
const jetBrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-jetbrains-mono',
  display: 'swap',
});

/**
 * Document metadata.
 *
 * PR feat/pwa-tier1-installable adds the iOS-Safari-specific
 * properties needed for a clean home-screen install on iPhone/iPad —
 * the web manifest covers Android/Chrome/Edge install, but iOS still
 * honors the older Apple meta tags for splash + standalone behavior.
 * Next auto-emits ``<link rel="manifest">`` from the ``manifest.ts``
 * route alongside these.
 */
export const metadata: Metadata = {
  title: 'Job Assist',
  description: 'Personal job-search aggregation and triage',
  appleWebApp: {
    capable: true,
    title: 'Job Assist',
    statusBarStyle: 'default',
  },
  icons: {
    apple: '/apple-touch-icon.png',
  },
};

/**
 * Viewport / theme-color (PR feat/pwa-tier1-installable).
 *
 * ``themeColor`` here drives the Android browser address-bar tint and
 * acts as a fallback for clients that haven't yet fetched the
 * manifest. The hex matches the manifest's ``theme_color`` (the
 * light-mode ``--primary`` token from globals.css).
 *
 * Next 15 wants viewport / themeColor in their own export rather than
 * piled into ``metadata``; this is the warning-free shape.
 */
export const viewport: Viewport = {
  themeColor: '#3b8fa9',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      // suppressHydrationWarning is required by next-themes — the
      // server can't know the resolved theme so a class diff on
      // <html> is expected on first paint.
      suppressHydrationWarning
      className={`${inter.variable} ${jetBrainsMono.variable}`}
    >
      <body className="min-h-screen font-sans">
        <ThemeProvider>
          <QueryProvider>
            {children}
            {/*
              PR #73 / Bestiary 5.14:
                - duration=2500 sets the global default for success/info.
                - closeButton lets the operator manually dismiss an error
                  toast early (or a success toast that lingers).
                - Errors override to 4500ms in showErrorToast for read
                  time. Sonner's library default for toast.error is
                  Infinity, which is the bug this PR closes.
            */}
            <Toaster position="bottom-right" duration={2500} closeButton />
          </QueryProvider>
        </ThemeProvider>
        {/*
          PR feat/pwa-tier1-installable: registers /sw.js after first
          mount. Renders no DOM. Production-only (HMR + SW collide in
          dev). The SW is non-essential — failure is silent + logged.
        */}
        <ServiceWorkerRegistrar />
      </body>
    </html>
  );
}
