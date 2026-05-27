import type { Metadata } from 'next';
import { Inter, JetBrains_Mono } from 'next/font/google';
import { Toaster } from 'sonner';

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

export const metadata: Metadata = {
  title: 'Job Assist',
  description: 'Personal job-search aggregation and triage',
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
      </body>
    </html>
  );
}
