import { forwardRef } from 'react';

import { cn } from '@/lib/utils';

/**
 * shadcn-style Card primitive (UX overhaul PR 1).
 *
 * Tuned to the codebase's existing oklch token system: ``bg-card``,
 * ``border-border``, ``shadow-card``. Pages currently use inline
 * ``bg-card border-border rounded-md`` patterns — this primitive
 * formalizes the contract so the same shape lands consistently when
 * pages migrate in PR 2/PR 3.
 *
 * Five sub-components:
 *   - ``<Card>``           — outer container
 *   - ``<CardHeader>``     — title + description bar
 *   - ``<CardTitle>``      — semibold sm-size
 *   - ``<CardDescription>`` — muted-foreground base
 *   - ``<CardContent>``    — body padding
 *   - ``<CardFooter>``     — trailing actions row
 *
 * Why ``forwardRef``: shadcn convention. Lets consumers attach refs
 * for measurement / focus management without dropping to ``as``-cast
 * gymnastics.
 */

export const Card = forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        'rounded-md border border-border bg-card text-card-foreground shadow-card',
        className,
      )}
      {...props}
    />
  ),
);
Card.displayName = 'Card';

export const CardHeader = forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('flex flex-col gap-1 p-4', className)} {...props} />
  ),
);
CardHeader.displayName = 'CardHeader';

export const CardTitle = forwardRef<HTMLHeadingElement, React.HTMLAttributes<HTMLHeadingElement>>(
  ({ className, ...props }, ref) => (
    // h3 by default — pages with multiple cards on a page should pass
    // ``as`` semantics through className where heading level matters.
    <h3
      ref={ref}
      className={cn('text-sm font-semibold leading-none tracking-tight', className)}
      {...props}
    />
  ),
);
CardTitle.displayName = 'CardTitle';

export const CardDescription = forwardRef<
  HTMLParagraphElement,
  React.HTMLAttributes<HTMLParagraphElement>
>(({ className, ...props }, ref) => (
  <p ref={ref} className={cn('text-base text-muted-foreground', className)} {...props} />
));
CardDescription.displayName = 'CardDescription';

export const CardContent = forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('p-4 pt-0', className)} {...props} />
  ),
);
CardContent.displayName = 'CardContent';

export const CardFooter = forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('flex items-center p-4 pt-0', className)} {...props} />
  ),
);
CardFooter.displayName = 'CardFooter';
