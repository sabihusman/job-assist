import { PipelineCard } from '@/components/pipeline/PipelineCard';
import { type PipelineStage, STAGE_LABELS } from '@/lib/applied/stages';
import type { ApplicationCard } from '@/lib/pipeline/bucket';

/**
 * Single kanban column. Fixed width (w-64); doesn't grow with content
 * length, scrolls vertically inside the body when overflowing.
 */
export function PipelineColumn({
  stage,
  cards,
}: {
  stage: PipelineStage;
  cards: readonly ApplicationCard[];
}) {
  // Stages display in the spec's casing (e.g. "Recruiter screen") —
  // map to short uppercase headers per UI_SPEC.md (RECRUITER, PHONE, …).
  const headerLabel =
    stage === 'recruiter' ? 'RECRUITER' : STAGE_LABELS[stage].split(' ')[0]!.toUpperCase();

  return (
    <section
      data-stage={stage}
      aria-label={STAGE_LABELS[stage]}
      className="flex w-64 shrink-0 flex-col gap-2 rounded-md border border-border bg-surface p-2.5"
    >
      <header className="flex items-center justify-between">
        <h3 className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
          {headerLabel}
        </h3>
        <span
          className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
          aria-label={`${cards.length} cards`}
        >
          {cards.length}
        </span>
      </header>
      {cards.length === 0 ? (
        <div
          data-testid={`pipeline-empty-${stage}`}
          className="flex h-20 items-center justify-center text-[14px] text-muted-foreground"
        >
          —
        </div>
      ) : (
        <ul className="flex list-none flex-col gap-3 p-0">
          {cards.map((c) => (
            <PipelineCard key={c.id} card={c} />
          ))}
        </ul>
      )}
    </section>
  );
}
