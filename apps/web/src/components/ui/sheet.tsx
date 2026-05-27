'use client';

import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { type ComponentPropsWithoutRef, type ElementRef, forwardRef } from 'react';

import { cn } from '@/lib/utils';

/**
 * Slim shadcn-style Sheet primitive (UX overhaul PR 1).
 *
 * Built on Radix Dialog (already a project dep — see dialog.tsx, ~80
 * LOC inline) with directional positioning and slide animation rather
 * than the center-modal default. Two consumers in PR 1:
 *
 *   1. Sidebar mobile drawer (``side="left"``) — Banner's hamburger
 *      opens it at <md breakpoint
 *   2. Triage DetailPanel mobile fallback (``side="bottom"``) — full-
 *      height sheet sliding up at <lg breakpoint, replacing the
 *      hidden-below-lg pattern that left mobile users with no detail
 *      surface
 *
 * The animation classes (data-[state=open]:slide-in-from-*) are wired
 * via tailwindcss-animate, already in the project's tailwind plugins.
 */
const Sheet = DialogPrimitive.Root;
const SheetTrigger = DialogPrimitive.Trigger;
const SheetClose = DialogPrimitive.Close;
const SheetPortal = DialogPrimitive.Portal;

const SheetOverlay = forwardRef<
  ElementRef<typeof DialogPrimitive.Overlay>,
  ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Overlay
    ref={ref}
    className={cn(
      'fixed inset-0 z-50 bg-black/50 backdrop-blur-sm',
      'data-[state=open]:animate-in data-[state=closed]:animate-out',
      'data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0',
      className,
    )}
    {...props}
  />
));
SheetOverlay.displayName = 'SheetOverlay';

type SheetSide = 'top' | 'right' | 'bottom' | 'left';

const SIDE_STYLES: Record<SheetSide, string> = {
  top: 'inset-x-0 top-0 border-b data-[state=open]:slide-in-from-top data-[state=closed]:slide-out-to-top',
  right:
    'inset-y-0 right-0 h-full w-3/4 max-w-sm border-l data-[state=open]:slide-in-from-right data-[state=closed]:slide-out-to-right',
  bottom:
    'inset-x-0 bottom-0 max-h-[90vh] border-t data-[state=open]:slide-in-from-bottom data-[state=closed]:slide-out-to-bottom',
  left: 'inset-y-0 left-0 h-full w-3/4 max-w-xs border-r data-[state=open]:slide-in-from-left data-[state=closed]:slide-out-to-left',
};

const SheetContent = forwardRef<
  ElementRef<typeof DialogPrimitive.Content>,
  ComponentPropsWithoutRef<typeof DialogPrimitive.Content> & {
    side?: SheetSide;
    hideCloseButton?: boolean;
    /**
     * Forwarded to the backdrop overlay. Use cases:
     *   - ``"lg:hidden"`` for a responsive Sheet that only renders
     *     below a breakpoint (the Triage DetailPanel mobile fallback).
     *     Without this, the overlay would still cover the viewport at
     *     lg+ even though SheetContent is hidden.
     */
    overlayClassName?: string;
  }
>(({ className, children, side = 'right', hideCloseButton, overlayClassName, ...props }, ref) => (
  <SheetPortal>
    <SheetOverlay className={overlayClassName} />
    <DialogPrimitive.Content
      ref={ref}
      className={cn(
        'fixed z-50 flex flex-col bg-surface text-foreground shadow-xl',
        'data-[state=open]:animate-in data-[state=closed]:animate-out duration-200',
        SIDE_STYLES[side],
        className,
      )}
      {...props}
    >
      {children}
      {!hideCloseButton && (
        <DialogPrimitive.Close
          className={cn(
            'absolute right-3 top-3 inline-flex h-7 w-7 items-center justify-center rounded',
            'text-muted-foreground hover:bg-accent hover:text-foreground',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          )}
        >
          <X className="h-4 w-4" />
          <span className="sr-only">Close</span>
        </DialogPrimitive.Close>
      )}
    </DialogPrimitive.Content>
  </SheetPortal>
));
SheetContent.displayName = 'SheetContent';

const SheetTitle = forwardRef<
  ElementRef<typeof DialogPrimitive.Title>,
  ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title ref={ref} className={cn('text-sm font-semibold', className)} {...props} />
));
SheetTitle.displayName = 'SheetTitle';

export { Sheet, SheetTrigger, SheetClose, SheetPortal, SheetOverlay, SheetContent, SheetTitle };
