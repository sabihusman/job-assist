import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

/**
 * Shared empty-state surface (UX overhaul PR 1).
 *
 * Replaces 11 page-local implementations (Triage, Applied, Passed,
 * Rejected, Companies, Contacts, Pipeline, Stats, Settings, plus a
 * couple of subcomponents) that drifted in copy and styling. Pages
 * adopt this incrementally in PR 2 and PR 3.
 *
 * Usage::
 *
 *     <EmptyState
 *       title="No passed postings yet."
 *       description="Postings you pass will land here."
 *       action={<button onClick={onReset}>Reset filters</button>}
 *       testId="passed-empty"
 *     />
 *
 * Visual contract:
 *   - Bordered card on ``bg-card``, centered text
 *   - Title in semibold sm-size
 *   - Description in muted-foreground base
 *   - Optional action slot renders below description
 *   - Optional ``icon`` slot above title (for future use; PR 1 leaves
 *     it unused — Linear-flavored aesthetic prefers text over chrome)
 */
export function EmptyState({
  title,
  description,
  action,
  icon,
  testId,
  className,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
  testId?: string;
  className?: string;
}) {
  return (
    <section
      data-testid={testId}
      className={cn(
        'flex flex-col items-center gap-2 rounded-md border border-border bg-card px-6 py-12 text-center',
        className,
      )}
    >
      {icon && (
        <div aria-hidden="true" className="text-muted-foreground">
          {icon}
        </div>
      )}
      <h2 className="text-sm font-semibold text-foreground">{title}</h2>
      {description && <p className="text-base text-muted-foreground">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </section>
  );
}
