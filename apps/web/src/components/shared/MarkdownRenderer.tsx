import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { cn } from '@/lib/utils';

/**
 * Centralised markdown renderer with our `remark-gfm` preset.
 *
 * Single source for prose styling — used by DetailPanel's JD section
 * and any future markdown surfaces (Settings descriptions, etc.).
 * The wrapper applies typography classes; the renderer itself is
 * passthrough.
 */
export function MarkdownRenderer({
  source,
  className,
}: {
  source: string;
  className?: string;
}) {
  return (
    <div className={cn('text-[13.5px] leading-[1.55] text-foreground/90', className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{source}</ReactMarkdown>
    </div>
  );
}
