'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { toast } from 'sonner';

import { API_BASE_URL } from '@/lib/api/client';
import { showErrorToast } from '@/lib/api/error-toast';
import { queryKeys } from '@/lib/api/hooks';
import type { ApplicationResumeMeta } from '@/lib/triage/types';

/**
 * Per-application resume attach (feat/application-resume Phase 1).
 *
 * Upload a .docx/.pdf OR paste text for THIS application — no global dropdown.
 * Upload posts the raw file bytes; paste posts JSON. Both go through the
 * same-origin /api/be proxy (token injected server-side), and the download is
 * a plain anchor through the proxy so the browser streams it natively — same
 * pattern as the xlsx export.
 */

const ACCEPT = '.docx,.pdf';

export function ResumeAttach({
  postingId,
  resume,
}: {
  postingId: string;
  resume: ApplicationResumeMeta | null;
}) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [pasteText, setPasteText] = useState('');
  const [busy, setBusy] = useState(false);

  const refresh = () => qc.invalidateQueries({ queryKey: queryKeys.posting(postingId) });
  const downloadHref = `${API_BASE_URL}/postings/${postingId}/resume`;

  async function uploadFile(file: File) {
    setBusy(true);
    try {
      const url = `${API_BASE_URL}/postings/${postingId}/resume?filename=${encodeURIComponent(
        file.name,
      )}`;
      const res = await fetch(url, {
        method: 'POST',
        body: file,
        headers: { 'content-type': file.type || 'application/octet-stream' },
      });
      if (!res.ok) throw new Error(`Upload failed (${res.status})`);
      toast.success('✓ Resume attached');
      await refresh();
    } catch (err) {
      showErrorToast(err, "Couldn't attach resume");
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  }

  async function savePaste() {
    if (!pasteText.trim()) return;
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE_URL}/postings/${postingId}/resume`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ resume_text: pasteText }),
      });
      if (!res.ok) throw new Error(`Save failed (${res.status})`);
      toast.success('✓ Resume text saved');
      setPasteText('');
      await refresh();
    } catch (err) {
      showErrorToast(err, "Couldn't save resume text");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mt-6 rounded-md border border-border bg-surface p-3" aria-label="Resume">
      <h3 className="text-[13px] font-semibold">Resume</h3>

      {/* Current attachment */}
      <div className="mt-1 text-[12px] text-muted-foreground">
        {resume?.has_file ? (
          <span>
            Attached:{' '}
            <a
              href={downloadHref}
              download
              data-testid="resume-download"
              className="text-foreground underline underline-offset-2 hover:text-primary"
            >
              {resume.file_name ?? 'resume'}
            </a>
          </span>
        ) : resume?.resume_text ? (
          <span>Text attached ({resume.resume_text.length} chars) — no file.</span>
        ) : (
          <span>None attached.</span>
        )}
      </div>

      {/* Upload (primary) */}
      <div className="mt-3 flex flex-col gap-1">
        <label htmlFor={`resume-file-${postingId}`} className="text-[11px] text-muted-foreground">
          Upload .docx / .pdf
        </label>
        <input
          id={`resume-file-${postingId}`}
          ref={fileRef}
          type="file"
          accept={ACCEPT}
          disabled={busy}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void uploadFile(f);
          }}
          className="text-[12px] file:mr-2 file:rounded file:border file:border-border file:bg-surface-2 file:px-2 file:py-0.5 file:text-[12px]"
        />
      </div>

      {/* Paste fallback */}
      <div className="mt-3 flex flex-col gap-1">
        <label htmlFor={`resume-text-${postingId}`} className="text-[11px] text-muted-foreground">
          …or paste text
        </label>
        <textarea
          id={`resume-text-${postingId}`}
          value={pasteText}
          onChange={(e) => setPasteText(e.target.value)}
          placeholder="Paste the tailored resume text…"
          className="min-h-[72px] w-full rounded-md border border-border bg-input px-2 py-1 text-[12px] outline-none focus:border-border-strong"
        />
        <div className="flex justify-end">
          <button
            type="button"
            onClick={() => void savePaste()}
            disabled={busy || !pasteText.trim()}
            className="inline-flex h-7 items-center rounded-md border border-border bg-surface-2 px-3 text-[12px] hover:bg-accent disabled:opacity-50"
          >
            Save text
          </button>
        </div>
      </div>
    </section>
  );
}
