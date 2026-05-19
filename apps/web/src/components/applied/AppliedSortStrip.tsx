'use client';

import { useRouter, useSearchParams } from 'next/navigation';

import type { AppliedSort } from '@/lib/applied/types';
import { cn } from '@/lib/utils';

/**
 * Three-pill sort toggle for the Applied page. Lives in URL search
 * params (`?sort=applied|stage|tier`) so the chosen sort is shareable.
 */
const OPTIONS: { value: AppliedSort; label: string }[] = [
  { value: 'applied', label: 'applied' },
  { value: 'stage', label: 'stage' },
  { value: 'tier', label: 'tier' },
];

export function AppliedSortStrip() {
  const router = useRouter();
  const params = useSearchParams();
  const active = (params.get('sort') as AppliedSort | null) ?? 'applied';

  const onPick = (value: AppliedSort) => {
    const next = new URLSearchParams(params.toString());
    if (value === 'applied') next.delete('sort');
    else next.set('sort', value);
    const search = next.toString();
    router.replace(search ? `/applied?${search}` : '/applied', { scroll: false });
  };

  return (
    <div className="flex items-center gap-1 font-mono text-[12px] text-muted-foreground">
      <span>sort:</span>
      {OPTIONS.map((o) => {
        const isActive = active === o.value;
        return (
          <button
            key={o.value}
            type="button"
            onClick={() => onPick(o.value)}
            data-active={isActive}
            aria-pressed={isActive}
            className={cn(
              'rounded px-2 py-0.5 transition-colors',
              isActive ? 'bg-surface-2 text-foreground' : 'hover:text-foreground',
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
