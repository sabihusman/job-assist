import { avatarBg, avatarInitial } from '@/lib/colors/avatar-hue';
import { cn } from '@/lib/utils';

/**
 * Deterministic company avatar. Same name → same hue across all
 * placements (TriageCard 32px, DetailPanel hero 56px, Pipeline cards
 * 32px, Companies table 32px).
 *
 * The visual contract is a colored rounded-md square with a single
 * white initial. The hue derivation lives in `lib/colors/avatar-hue`;
 * this component is just the presentation layer.
 */
type Size = 32 | 56;

const SIZE_CLASSES: Record<Size, string> = {
  32: 'h-8 w-8 text-[13px]',
  56: 'h-14 w-14 text-[20px]',
};

export function CompanyAvatar({
  name,
  size = 32,
  className,
}: {
  name: string;
  size?: Size;
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'flex shrink-0 items-center justify-center rounded-md font-semibold text-white',
        SIZE_CLASSES[size],
        className,
      )}
      style={{ background: avatarBg(name) }}
    >
      {avatarInitial(name)}
    </span>
  );
}
