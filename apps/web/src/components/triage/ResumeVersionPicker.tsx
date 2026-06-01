'use client';

import { useEffect } from 'react';

import { useResumeVersions } from '@/lib/api/resume';
import { cn } from '@/lib/utils';

/**
 * Inline resume-version picker shown when the operator applies to a
 * posting (key 3 / the Applied button). Mirrors ReasonPicker: a small
 * overlay whose own key listener takes over while it's mounted (the
 * Triage keyboard is paused by the consuming page via ``enabled``).
 *
 * "Which resume version did you send?" — choose one, or Skip (apply
 * untagged). Esc also skips. Numeric hotkeys 1-9 pick the first nine
 * versions; ``0`` / Esc = skip. The selection's id is passed up so the
 * apply mutation carries ``resume_version_id``.
 *
 * The list is fetched lazily (``enabled`` only while open) so Triage
 * doesn't pay for it until the operator actually applies.
 */

function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable;
}

export function ResumeVersionPicker({
  onSelect,
  onSkip,
  className,
}: {
  onSelect: (resumeVersionId: string) => void;
  onSkip: () => void;
  className?: string;
}) {
  const { data, isLoading } = useResumeVersions(true);
  const versions = data?.items ?? [];

  useEffect(() => {
    const listener = (e: KeyboardEvent) => {
      if (isEditable(e.target)) return;
      if (e.key === 'Escape' || e.key === '0') {
        e.preventDefault();
        onSkip();
        return;
      }
      // 1-9 → pick the nth listed version (if present).
      const n = Number.parseInt(e.key, 10);
      if (!Number.isNaN(n) && n >= 1 && n <= 9 && versions[n - 1]) {
        e.preventDefault();
        onSelect(versions[n - 1].id);
      }
    };
    window.addEventListener('keydown', listener);
    return () => window.removeEventListener('keydown', listener);
  }, [versions, onSelect, onSkip]);

  return (
    <div
      data-testid="resume-version-picker"
      className={cn('mt-2 rounded-md border border-border bg-surface-2 p-2 text-[13px]', className)}
    >
      <div className="mb-1.5 px-1 text-[12px] text-muted-foreground">
        Which resume version did you send?{' '}
        <span className="text-muted-foreground/70">(Esc / 0 = skip)</span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {isLoading ? (
          <span className="px-2 py-1 text-muted-foreground">Loading…</span>
        ) : versions.length === 0 ? (
          <span className="px-2 py-1 text-muted-foreground">
            No resume versions yet — create one on the Resumes page.
          </span>
        ) : (
          versions.slice(0, 9).map((v, i) => (
            <button
              key={v.id}
              type="button"
              onClick={() => onSelect(v.id)}
              className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-1 hover:bg-accent"
              title={v.angle ?? undefined}
            >
              <span className="text-muted-foreground/70">{i + 1}</span>
              {v.label}
            </button>
          ))
        )}
        <button
          type="button"
          onClick={onSkip}
          className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-muted-foreground hover:bg-accent"
        >
          <span className="text-muted-foreground/70">0</span>
          Skip
        </button>
      </div>
    </div>
  );
}
