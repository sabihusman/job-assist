'use client';

import { useState } from 'react';
import { toast } from 'sonner';

import { AppShell } from '@/components/chrome/AppShell';
import { showErrorToast } from '@/lib/api/error-toast';
import { useCreateResumeVersion, useResumeAnalytics, useResumeVersions } from '@/lib/api/resume';

/**
 * Resume-version manager + outcome analytics (feat/resume-version-tracking).
 *
 * Top: create + list tailored resume variants (label, angle, optional
 * snapshot text, notes). Bottom: the resume→outcome analytics —
 * rejection/confirmation rate by version + funnel depth + the
 * company-level ambiguity flag. The analytic is company-level (see the
 * attribution note rendered from the API).
 */
export default function ResumesPage() {
  return (
    <AppShell title="Resumes" subtitle="Tailored variants + outcome analytics">
      <div className="flex min-w-0 flex-col gap-6 px-4 py-4 md:px-6">
        <CreateForm />
        <VersionList />
        <Analytics />
      </div>
    </AppShell>
  );
}

function CreateForm() {
  const create = useCreateResumeVersion();
  const [label, setLabel] = useState('');
  const [angle, setAngle] = useState('');
  const [snapshot, setSnapshot] = useState('');
  const [notes, setNotes] = useState('');

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!label.trim()) return;
    create.mutate(
      {
        label: label.trim(),
        angle: angle.trim() || null,
        snapshot_text: snapshot.trim() || null,
        notes: notes.trim() || null,
      },
      {
        onSuccess: () => {
          toast.success(`✓ Created ${label.trim()}`);
          setLabel('');
          setAngle('');
          setSnapshot('');
          setNotes('');
        },
        onError: (err) => showErrorToast(err, "Couldn't create resume version"),
      },
    );
  };

  return (
    <form
      onSubmit={submit}
      className="rounded-md border border-border bg-surface p-4"
      data-testid="resume-create-form"
    >
      <h2 className="mb-3 text-sm font-semibold">New resume version</h2>
      <div className="flex flex-col gap-2">
        <input
          aria-label="label"
          placeholder="label (e.g. betterment-trust-v1)"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          className="rounded-md border border-border bg-surface-2 px-2 py-1 text-[13px]"
        />
        <input
          aria-label="angle"
          placeholder="angle / tailoring thesis (optional)"
          value={angle}
          onChange={(e) => setAngle(e.target.value)}
          className="rounded-md border border-border bg-surface-2 px-2 py-1 text-[13px]"
        />
        <textarea
          aria-label="snapshot_text"
          placeholder="resume text snapshot (optional — for content correlation)"
          value={snapshot}
          onChange={(e) => setSnapshot(e.target.value)}
          rows={3}
          className="rounded-md border border-border bg-surface-2 px-2 py-1 text-[13px]"
        />
        <input
          aria-label="notes"
          placeholder="notes (optional)"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          className="rounded-md border border-border bg-surface-2 px-2 py-1 text-[13px]"
        />
        <button
          type="submit"
          disabled={!label.trim() || create.isPending}
          className="self-start rounded-md bg-primary px-3 py-1 text-[13px] text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {create.isPending ? 'Creating…' : 'Create'}
        </button>
      </div>
    </form>
  );
}

function VersionList() {
  const { data, isLoading, isError } = useResumeVersions();
  const items = data?.items ?? [];
  return (
    <section data-testid="resume-list">
      <h2 className="mb-2 text-sm font-semibold">Versions ({items.length})</h2>
      {isLoading ? (
        <p className="text-[13px] text-muted-foreground">Loading…</p>
      ) : isError ? (
        <p className="text-[13px] text-negative">Couldn&apos;t load versions.</p>
      ) : items.length === 0 ? (
        <p className="text-[13px] text-muted-foreground">No versions yet.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((v) => (
            <li
              key={v.id}
              className="rounded-md border border-border bg-surface px-3 py-2 text-[13px]"
            >
              <div className="font-medium">{v.label}</div>
              {v.angle && <div className="text-muted-foreground">{v.angle}</div>}
              {v.notes && <div className="text-muted-foreground/80">{v.notes}</div>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Analytics() {
  const { data, isLoading, isError } = useResumeAnalytics();
  return (
    <section data-testid="resume-analytics">
      <h2 className="mb-2 text-sm font-semibold">Outcome analytics</h2>
      {isLoading ? (
        <p className="text-[13px] text-muted-foreground">Loading…</p>
      ) : isError || !data ? (
        <p className="text-[13px] text-negative">Couldn&apos;t load analytics.</p>
      ) : (
        <div className="flex flex-col gap-3">
          {/* Ambiguity caveat — surfaced prominently so the numbers are read with care. */}
          {data.ambiguous_companies.length > 0 && (
            <div className="rounded-md border border-negative/30 bg-negative/5 p-2 text-[12px]">
              ⚠ {data.ambiguous_companies.length} company(ies) received more than one resume version
              — their outcomes can&apos;t be cleanly attributed to a single version (outcomes link
              at company level). Read those rows with caution.
            </div>
          )}
          <p className="text-[12px] text-muted-foreground">{data.attribution_note}</p>

          {data.by_version.length === 0 ? (
            <p className="text-[13px] text-muted-foreground">
              No tagged applications yet — apply to a posting and pick a resume version.
            </p>
          ) : (
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-border border-b text-left text-muted-foreground">
                  <th className="py-1 pr-3">Version</th>
                  <th className="py-1 pr-3">Apps</th>
                  <th className="py-1 pr-3">Companies</th>
                  <th className="py-1 pr-3">Rejected</th>
                  <th className="py-1 pr-3">Confirmed</th>
                </tr>
              </thead>
              <tbody>
                {data.by_version.map((r) => (
                  <tr key={r.resume_version_id} className="border-border/50 border-b">
                    <td className="py-1 pr-3 font-medium">{r.label}</td>
                    <td className="py-1 pr-3">{r.applications}</td>
                    <td className="py-1 pr-3">{r.companies}</td>
                    <td className="py-1 pr-3 text-negative">{r.companies_rejected}</td>
                    <td className="py-1 pr-3 text-positive">{r.companies_confirmed}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {data.funnel.length > 0 && (
            <details className="text-[13px]">
              <summary className="cursor-pointer text-muted-foreground">
                Funnel depth by version
              </summary>
              <ul className="mt-1 flex flex-col gap-0.5 pl-3">
                {data.funnel.map((f, i) => (
                  <li key={`${f.label}-${f.outcome_type}-${i}`}>
                    <span className="font-medium">{f.label}</span> ·{' '}
                    <span className="text-muted-foreground">{f.outcome_type}</span> · {f.companies}{' '}
                    co.
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </section>
  );
}
