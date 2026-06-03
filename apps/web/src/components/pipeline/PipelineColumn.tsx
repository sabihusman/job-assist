import { ChevronLeft, ChevronRight } from 'lucide-react';

import { PipelineCard } from '@/components/pipeline/PipelineCard';
import { type PipelineStage, STAGE_LABELS } from '@/lib/applied/stages';
import type { ApplicationCard } from '@/lib/pipeline/bucket';

/**
 * Single kanban column. Fixed width (w-64); doesn't grow with content
 * length, scrolls vertically inside the body when overflowing.
 *
 * feat/pipeline-detail: cards are clickable via `onSelect`.
 * feat/pipeline-reorder: header chevrons move the column earlier/later via
 * `onMove` (no-dep reorder; drag is a later nicety).
 */
export function PipelineColumn({
  stage,
  cards,
  onSelect,
  onMove,
  canMoveEarlier = false,
  canMoveLater = false,
}: {
  stage: PipelineStage;
  cards: readonly ApplicationCard[];
  onSelect?: (cardId: string) => void;
  onMove?: (dir: 'up' | 'down') => void;
  canMoveEarlier?: boolean;
  canMoveLater?: boolean;
}) {
  // Stages display in the spec's casing (e.g. "Recruiter screen") —
  // map to short uppercase headers per UI_SPEC.md (RECRUITER, PHONE, …).
  const headerLabel =
    stage === 'recruiter' ? 'RECRUITER' : (STAGE_LABELS[stage].split(' ')[0] ?? '').toUpperCase();

  return (
    <section
      data-stage={stage}
      aria-label={STAGE_LABELS[stage]}
      className="flex w-64 shrink-0 flex-col gap-2 rounded-md border border-border bg-surface p-2.5"
    >
      <header className="flex items-center justify-between gap-1">
        <h3 className="flex-1 truncate font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
          {headerLabel}
        </h3>
        {onMove && (
          <div className="flex items-center">
            <button
              type="button"
              disabled={!canMoveEarlier}
              onClick={() => onMove('up')}
              aria-label={`Move ${STAGE_LABELS[stage]} column left`}
              className="inline-flex h-5 w-5 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-30"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              disabled={!canMoveLater}
              onClick={() => onMove('down')}
              aria-label={`Move ${STAGE_LABELS[stage]} column right`}
              className="inline-flex h-5 w-5 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-30"
            >
              <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>
        )}
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
            <PipelineCard
              key={c.id}
              card={c}
              onSelect={onSelect ? () => onSelect(c.id) : undefined}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
