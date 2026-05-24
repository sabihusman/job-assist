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
