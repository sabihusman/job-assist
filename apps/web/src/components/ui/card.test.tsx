import { render, screen } from '@testing-library/react';
import { createRef } from 'react';
import { describe, expect, test } from 'vitest';

import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';

/**
 * Card primitive contract (UX overhaul PR 1).
 *
 * Mirrors the shape of the existing local shadcn Card pattern (see
 * components/ui/dialog.tsx). Tests pin the five sub-components compose
 * into a single card surface and forwardRef wiring works.
 */
describe('Card primitive', () => {
  test('renders Card wrapping CardHeader / CardContent / CardFooter', () => {
    render(
      <Card data-testid="card-root">
        <CardHeader>
          <CardTitle>Title text</CardTitle>
          <CardDescription>Description text</CardDescription>
        </CardHeader>
        <CardContent>
          <p>Body</p>
        </CardContent>
        <CardFooter>
          <button type="button">Action</button>
        </CardFooter>
      </Card>,
    );

    expect(screen.getByTestId('card-root')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Title text' })).toBeInTheDocument();
    expect(screen.getByText('Description text')).toBeInTheDocument();
    expect(screen.getByText('Body')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Action' })).toBeInTheDocument();
  });

  test('forwards ref to the outer div', () => {
    const ref = createRef<HTMLDivElement>();
    render(<Card ref={ref} data-testid="card-ref" />);
    expect(ref.current).not.toBeNull();
    expect(ref.current?.tagName).toBe('DIV');
  });

  test('forwards refs to header subcomponents', () => {
    const titleRef = createRef<HTMLHeadingElement>();
    const descRef = createRef<HTMLParagraphElement>();
    render(
      <Card>
        <CardHeader>
          <CardTitle ref={titleRef}>x</CardTitle>
          <CardDescription ref={descRef}>y</CardDescription>
        </CardHeader>
      </Card>,
    );
    // CardTitle defaults to h3; CardDescription to p.
    expect(titleRef.current?.tagName).toBe('H3');
    expect(descRef.current?.tagName).toBe('P');
  });

  test('passes className through Card', () => {
    render(<Card data-testid="card-cls" className="custom-cls" />);
    expect(screen.getByTestId('card-cls')).toHaveClass('custom-cls');
    // Should also retain default token classes.
    expect(screen.getByTestId('card-cls')).toHaveClass('bg-card');
  });
});
