import { SettingsSection } from '@/components/settings/layout';

/**
 * API keys — read-only display of env-var presence. Hardcoded "set"
 * for all 5 keys per the PR #32d audit (no backend env-status
 * endpoint exists). The spec's "missing" / red-dot variant and the
 * "Week 4" inline tag are stripped here too.
 *
 * Replacing the hardcoded set with a real status check is one of the
 * follow-ups documented in the PR description.
 */

const ENV_VARS = [
  'DATABASE_URL',
  'GEMINI_API_KEY',
  'ANTHROPIC_API_KEY',
  'GMAIL_CREDENTIALS_JSON',
  'GMAIL_REFRESH_TOKEN',
];

export function ApiKeysSection() {
  return (
    <SettingsSection
      heading="API keys"
      description="Read-only · values are stored on the backend and not retrievable."
    >
      <ul className="flex list-none flex-col gap-1 p-0">
        {ENV_VARS.map((name) => (
          <li
            key={name}
            className="flex items-center justify-between rounded border border-border bg-card px-3 py-2 text-[13px]"
          >
            <span className="font-mono">{name}</span>
            <span className="inline-flex items-center gap-1.5 rounded bg-surface-2 px-1.5 py-0.5 text-[11px] text-muted-foreground">
              <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-positive" />
              set
            </span>
          </li>
        ))}
      </ul>
    </SettingsSection>
  );
}
