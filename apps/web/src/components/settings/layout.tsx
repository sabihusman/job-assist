import type { ReactNode } from 'react';

/**
 * Shared scaffolding for Settings sections + rows.
 *
 * Every section is the same shape:
 *   <h2>Heading</h2>
 *   <p>Description</p>
 *   <Row label="..." sub="...">control</Row>
 *   <Row label="..." sub="...">control</Row>
 *
 * Pulling the chrome here keeps each section's file focused on its
 * controls instead of redundant heading markup.
 */

export function SettingsSection({
  heading,
  description,
  children,
}: {
  heading: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="border-b border-border py-8 first:pt-4">
      <h2 className="text-[15px] font-semibold">{heading}</h2>
      {description && <p className="mt-1 text-[13px] text-muted-foreground">{description}</p>}
      <div className="mt-6 flex flex-col gap-6">{children}</div>
    </section>
  );
}

export function SettingsRow({
  label,
  sub,
  children,
}: {
  label: string;
  sub?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-[220px_1fr] md:items-start">
      <div className="flex flex-col gap-0.5">
        <span className="text-[13px] font-medium">{label}</span>
        {sub && <span className="text-[12px] text-muted-foreground">{sub}</span>}
      </div>
      <div>{children}</div>
    </div>
  );
}
