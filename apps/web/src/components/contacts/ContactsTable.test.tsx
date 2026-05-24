import { render, screen, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { ContactsTable } from '@/components/contacts/ContactsTable';
import type { ContactListItem } from '@/lib/contacts/types';

// PII discipline: every fixture below uses obviously-fake names.

function makeContact(overrides: Partial<ContactListItem> = {}): ContactListItem {
  return {
    id: `c-${Math.random().toString(36).slice(2, 8)}`,
    first_name: 'Test',
    last_name: 'Person',
    preferred_first_name: null,
    email_primary: 'test@example.test',
    email_secondary: null,
    linkedin_url: null,
    current_employer: 'ExampleCorp',
    current_position: 'Senior PM',
    location_city: null,
    location_state: null,
    location_country: null,
    location_metro: null,
    source_type: 'tippie_alumni',
    target_company_id: null,
    archived_at: null,
    created_at: '2026-05-30T00:00:00Z',
    ...overrides,
  };
}

describe('ContactsTable', () => {
  test('renders a row per contact', () => {
    render(
      <ContactsTable
        contacts={[
          makeContact({ first_name: 'Test', last_name: 'One' }),
          makeContact({ first_name: 'Test', last_name: 'Two' }),
        ]}
        showingArchived={false}
      />,
    );
    expect(screen.getAllByTestId('contact-row')).toHaveLength(2);
  });

  test('shows the empty state when no contacts are passed', () => {
    render(<ContactsTable contacts={[]} showingArchived={false} />);
    expect(screen.getByTestId('contacts-empty')).toBeInTheDocument();
    expect(screen.getByText(/no contacts yet/i)).toBeInTheDocument();
  });

  test('empty state copy adapts when showingArchived=true', () => {
    render(<ContactsTable contacts={[]} showingArchived={true} />);
    expect(screen.getByText(/no contacts in this view/i)).toBeInTheDocument();
  });

  test('renders source chip with the operator-facing label', () => {
    render(
      <ContactsTable
        contacts={[makeContact({ source_type: 'recruiter_inbound' })]}
        showingArchived={false}
      />,
    );
    expect(screen.getByText('Recruiter inbound')).toBeInTheDocument();
  });

  test('email link uses mailto: when email_primary is present', () => {
    render(
      <ContactsTable
        contacts={[makeContact({ email_primary: 'someone@example.test' })]}
        showingArchived={false}
      />,
    );
    const link = screen.getByRole('link', { name: /Email Test Person/i });
    expect(link.getAttribute('href')).toBe('mailto:someone@example.test');
  });

  test('LinkedIn link opens in new tab with rel attributes when present', () => {
    render(
      <ContactsTable
        contacts={[
          makeContact({
            email_primary: null,
            linkedin_url: 'https://linkedin.com/in/test-person',
          }),
        ]}
        showingArchived={false}
      />,
    );
    const link = screen.getByRole('link', { name: /Open LinkedIn for Test Person/i });
    expect(link.getAttribute('href')).toBe('https://linkedin.com/in/test-person');
    expect(link.getAttribute('target')).toBe('_blank');
    expect(link.getAttribute('rel')).toContain('noopener');
  });

  test('renders em-dash when neither email nor LinkedIn is present', () => {
    // Synthesise a row that lacks both — DB CHECK constraint normally
    // prevents this, but the component must degrade gracefully if it
    // ever happens (e.g. partial fetch, stale cache).
    render(
      <ContactsTable
        contacts={[makeContact({ email_primary: null, linkedin_url: null })]}
        showingArchived={false}
      />,
    );
    const row = screen.getByTestId('contact-row');
    // Row contains the em-dash glyph in the Channels cell.
    expect(within(row).getByText('—')).toBeInTheDocument();
  });

  test('preferred_first_name shown inline with legal first name', () => {
    render(
      <ContactsTable
        contacts={[
          makeContact({
            first_name: 'Robert',
            preferred_first_name: 'Bobby',
            last_name: 'Smith',
          }),
        ]}
        showingArchived={false}
      />,
    );
    expect(screen.getByText('Robert (Bobby) Smith')).toBeInTheDocument();
  });

  test('preferred_first_name is suppressed when it equals first_name', () => {
    render(
      <ContactsTable
        contacts={[
          makeContact({
            first_name: 'Jamie',
            preferred_first_name: 'Jamie',
            last_name: 'Doe',
          }),
        ]}
        showingArchived={false}
      />,
    );
    expect(screen.getByText('Jamie Doe')).toBeInTheDocument();
  });

  test('archived row is marked data-archived=true and renders at reduced opacity', () => {
    render(
      <ContactsTable
        contacts={[makeContact({ archived_at: '2026-05-25T00:00:00Z' })]}
        showingArchived={true}
      />,
    );
    const row = screen.getByTestId('contact-row');
    expect(row.getAttribute('data-archived')).toBe('true');
    expect(row.className).toContain('opacity-60');
  });

  test('non-archived row is marked data-archived=false', () => {
    render(
      <ContactsTable contacts={[makeContact({ archived_at: null })]} showingArchived={false} />,
    );
    const row = screen.getByTestId('contact-row');
    expect(row.getAttribute('data-archived')).toBe('false');
  });
});
