import type { ReactNode } from 'react';

/**
 * Page-body placeholder for #32a's six routes. The chrome (sidebar,
 * banner, command palette) is already wired; this card sits in the
 * main scroll region with a note about which follow-up PR lands the
 * real content.
 *
 * `extra` renders above the placeholder — Settings uses it for the
 * working `<ThemeToggle />` since the Appearance section is the only
 * real interaction available before #32c.
 */
export function PlaceholderPage({
  heading,
  body,
  extra,
}: {
  heading: string;
  body: string;
  extra?: ReactNode;
}) {
  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6 px-6 py-12">
      {extra && (
        <section className="rounded-md border border-border bg-card p-6 shadow-card">
          {extra}
        </section>
      )}
      <section
        data-testid="placeholder-card"
        className="rounded-md border border-border bg-card p-8 text-center shadow-card"
      >
        <h2 className="text-base font-semibold">{heading}</h2>
        <p className="mt-2 text-sm text-muted-foreground">{body}</p>
      </section>
    </div>
  );
}
