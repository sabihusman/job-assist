/**
 * Wire types for the outreach contacts list (PR #51).
 *
 * Mirror of ``apps/api/src/job_assist/schemas/contact.py::ContactListItem``.
 * Same convention as ``lib/triage/types.ts``: the FastAPI endpoint
 * returns ``dict[str, Any]`` so openapi-typescript generates loose
 * shapes — we keep the contract stable by typing it here.
 */

export type ContactSourceType =
  | 'tippie_alumni'
  | 'linkedin_outreach'
  | 'recruiter_inbound'
  | 'warm_intro';

export type ContactListItem = {
  id: string;
  first_name: string;
  last_name: string;
  preferred_first_name: string | null;
  email_primary: string | null;
  email_secondary: string | null;
  linkedin_url: string | null;
  current_employer: string | null;
  current_position: string | null;
  location_city: string | null;
  location_state: string | null;
  location_country: string | null;
  location_metro: string | null;
  source_type: ContactSourceType | string;
  target_company_id: string | null;
  archived_at: string | null;
  created_at: string;
};

export type ContactsListResponse = {
  total: number;
  offset: number;
  limit: number;
  items: ContactListItem[];
};

export type ContactsFilters = {
  source_type: ContactSourceType[];
  search: string;
  include_archived: boolean;
  limit: number;
  offset: number;
};

export const DEFAULT_CONTACTS_FILTERS: ContactsFilters = {
  source_type: [],
  search: '',
  include_archived: false,
  limit: 50,
  offset: 0,
};

export const CONTACT_SOURCE_LABELS: Record<ContactSourceType, string> = {
  tippie_alumni: 'Tippie alumni',
  linkedin_outreach: 'LinkedIn outreach',
  recruiter_inbound: 'Recruiter inbound',
  warm_intro: 'Warm intro',
};

// ── PR #52 — detail + CRUD + outreach types ────────────────────────────────

/**
 * Full ContactDetail shape returned by ``GET /contacts/{id}``,
 * ``POST /contacts``, and ``PATCH /contacts/{id}``. Strict superset
 * of ``ContactListItem`` — see backend ``schemas/contact.py``.
 */
export type ContactDetail = ContactListItem & {
  phone: string | null;
  source_metadata: Record<string, unknown> | null;
  job_functions_of_interest: string[] | null;
  industries_of_interest: string[] | null;
  contact_opt_in: boolean;
  contact_opt_in_topics: string[] | null;
  notes: string | null;
  updated_at: string;
};

/**
 * Body for ``POST /contacts`` — operator-driven create.
 * Mirrors ``schemas/contact.py::ContactCreate``.
 */
export type ContactCreate = {
  first_name: string;
  last_name: string;
  preferred_first_name?: string | null;
  email_primary?: string | null;
  email_secondary?: string | null;
  linkedin_url?: string | null;
  phone?: string | null;
  current_employer?: string | null;
  current_position?: string | null;
  location_city?: string | null;
  location_state?: string | null;
  location_country?: string | null;
  location_metro?: string | null;
  source_type: ContactSourceType | string;
  source_metadata?: Record<string, unknown> | null;
  job_functions_of_interest?: string[] | null;
  industries_of_interest?: string[] | null;
  contact_opt_in?: boolean;
  contact_opt_in_topics?: string[] | null;
  notes?: string | null;
  target_company_id?: string | null;
};

/**
 * Body for ``PATCH /contacts/{id}`` — partial update.
 *
 * Every field is optional. ``undefined`` (key absent) ≠ ``null``
 * (key present, clearing the value). The wire serializer must
 * strip ``undefined`` keys before sending so the PATCH semantics
 * line up with the FastAPI ``exclude_unset=True`` apply.
 *
 * Immutable fields (``id``, ``created_at``, ``source_type``,
 * ``first_name``, ``last_name``) are deliberately absent — the
 * API's ``extra='forbid'`` rejects them with 422.
 */
export type ContactUpdate = {
  preferred_first_name?: string | null;
  email_primary?: string | null;
  email_secondary?: string | null;
  linkedin_url?: string | null;
  phone?: string | null;
  current_employer?: string | null;
  current_position?: string | null;
  location_city?: string | null;
  location_state?: string | null;
  location_country?: string | null;
  location_metro?: string | null;
  source_metadata?: Record<string, unknown> | null;
  job_functions_of_interest?: string[] | null;
  industries_of_interest?: string[] | null;
  contact_opt_in?: boolean;
  contact_opt_in_topics?: string[] | null;
  notes?: string | null;
  target_company_id?: string | null;
};

// ── outreach_message ─────────────────────────────────────────────────────────

export type MessageDirection = 'outbound' | 'inbound';
export type MessageChannel = 'email' | 'linkedin' | 'other';
export type MessageSource = 'manual' | 'gmail_auto';

export type OutreachMessage = {
  id: string;
  contact_id: string;
  direction: MessageDirection | string;
  channel: MessageChannel | string;
  subject: string | null;
  body: string | null;
  sent_at: string;
  posting_id: string | null;
  source: MessageSource | string;
  external_message_id: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string;
};

export type OutreachMessageListResponse = {
  total: number;
  offset: number;
  limit: number;
  items: OutreachMessage[];
};

export type OutreachRecentItem = OutreachMessage & {
  contact_first_name: string;
  contact_last_name: string;
  contact_source_type: ContactSourceType | string;
};

export type OutreachRecentResponse = {
  total: number;
  offset: number;
  limit: number;
  items: OutreachRecentItem[];
};

/**
 * Body for ``POST /contacts/{contact_id}/outreach``.
 *
 * ``source`` is intentionally absent — the server forces it to
 * ``'manual'``. The wire-shape contract test pins this.
 */
export type OutreachMessageCreate = {
  direction: MessageDirection;
  channel: MessageChannel;
  sent_at: string;
  subject?: string | null;
  body?: string | null;
  posting_id?: string | null;
  metadata?: Record<string, unknown> | null;
};

export const MESSAGE_DIRECTION_LABELS: Record<MessageDirection, string> = {
  outbound: 'Outbound',
  inbound: 'Inbound',
};

export const MESSAGE_CHANNEL_LABELS: Record<MessageChannel, string> = {
  email: 'Email',
  linkedin: 'LinkedIn',
  other: 'Other',
};
