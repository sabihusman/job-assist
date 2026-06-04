'use client';

import { useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import { toast } from 'sonner';

import { ConfirmRulesModal, type RuleChange } from '@/components/settings/ConfirmRulesModal';
import { SettingsRow, SettingsSection } from '@/components/settings/layout';
import { useUpdateProfile } from '@/lib/api/settings';
import {
  CLOSED_CHANNELS_STUB,
  DEFAULT_ROLE_FAMILY_WEIGHTS,
  type OperatorProfileRead,
  type RoleFamilyWeights,
  SENIORITY_LEVELS,
} from '@/lib/settings/types';
import { cn } from '@/lib/utils';

/**
 * Hard rule thresholds. Five subsections:
 *   - Maximum applicant count    (numeric + slider, backend column)
 *   - Salary floor               (numeric + slider, backend column)
 *   - Closed channels            (read-only stub; no backend API)
 *   - Staffing firm blocklist    (textarea, backend column)
 *   - Role family weights        (frontend-only state; no backend column)
 *
 * Save button is the only dirty-aware control in the entire Settings
 * page (per UI_SPEC.md). Clicking it on a dirty form opens the
 * confirm modal; clean state is a no-op.
 */

type HardRulesFormState = {
  applicant_cap: number;
  // feat/tunable-per-company-cap: roles surfaced per company; 0 = unlimited.
  per_company_cap: number;
  // Slice 2b: semantic blend weight 0..1; 0 = off (heuristic-only ranking).
  similarity_weight: number;
  salary_floor_usd: number;
  // PR #43: nullable ceiling. The form represents "no ceiling" as 0 so the
  // numeric input works naturally; the save handler converts 0 back to null.
  salary_ceiling_usd: number;
  // PR #43: which SeniorityLevel enum values to include. Empty = no filter.
  seniority_levels_included: string[];
  staffing_firm_blocklist: string; // textarea: newline-joined
  role_family_weights: RoleFamilyWeights;
};

const FAMILY_LABELS: Record<keyof RoleFamilyWeights, string> = {
  product_management: 'Product Management',
  product_owner: 'Product Owner',
  product_marketing: 'Product Marketing',
  program_management: 'Program Manager',
};

export function HardRulesSection({ profile }: { profile: OperatorProfileRead }) {
  const form = useForm<HardRulesFormState>({
    defaultValues: {
      applicant_cap: profile.applicant_cap,
      per_company_cap: profile.per_company_cap,
      // Coalesce to 0 (= off) so the numeric field + displayFormat always
      // bind to a real number. A profile served by an API that predates the
      // similarity_weight response field would otherwise feed `undefined`
      // into `n.toFixed(1)` and crash the entire Settings page render.
      similarity_weight: profile.similarity_weight ?? 0,
      salary_floor_usd: profile.salary_floor_usd,
      // PR #43: backend stores null when unset; surface that as 0 in the
      // form so the numeric input has a real value to bind to.
      salary_ceiling_usd: profile.salary_ceiling_usd ?? 0,
      seniority_levels_included: profile.seniority_levels_included ?? [],
      staffing_firm_blocklist: profile.staffing_firm_blocklist.join('\n'),
      role_family_weights: { ...DEFAULT_ROLE_FAMILY_WEIGHTS },
    },
  });
  const { control, register, handleSubmit, formState, watch, reset, getValues } = form;
  const update = useUpdateProfile();
  const [modalOpen, setModalOpen] = useState(false);

  const isDirty = formState.isDirty;

  const onSaveClick = () => {
    if (!isDirty) return;
    setModalOpen(true);
  };

  const onConfirmSave = async () => {
    const values = getValues();
    // PR #43: convert "no ceiling" (0 in the form) back to null on the
    // wire — the backend stores NULL to mean "rule disabled".
    const ceiling = values.salary_ceiling_usd > 0 ? values.salary_ceiling_usd : null;
    const body = {
      applicant_cap: values.applicant_cap,
      per_company_cap: values.per_company_cap,
      similarity_weight: values.similarity_weight,
      salary_floor_usd: values.salary_floor_usd,
      salary_ceiling_usd: ceiling,
      seniority_levels_included: values.seniority_levels_included,
      staffing_firm_blocklist: values.staffing_firm_blocklist
        .split('\n')
        .map((s) => s.trim())
        .filter((s) => s.length > 0),
    };
    try {
      await update.mutateAsync(body);
      toast.success('✓ Rules saved');
      setModalOpen(false);
      // Reset isDirty to false now that the new values match the backend.
      reset(values);
    } catch {
      // Modal stays open; error message shows inline.
    }
  };

  // Build the field-diff list for the modal. Skip role_family_weights
  // (per spec — too noisy to enumerate four sliders) and only include
  // fields whose dirty flag is set.
  const changes: RuleChange[] = [];
  if (formState.dirtyFields.applicant_cap) {
    changes.push({
      label: 'Maximum applicant count',
      from: String(profile.applicant_cap),
      to: String(watch('applicant_cap')),
    });
  }
  if (formState.dirtyFields.per_company_cap) {
    const capNow = watch('per_company_cap');
    changes.push({
      label: 'Roles per company',
      from: profile.per_company_cap === 0 ? 'Unlimited' : String(profile.per_company_cap),
      to: capNow === 0 ? 'Unlimited' : String(capNow),
    });
  }
  if (formState.dirtyFields.similarity_weight) {
    const wNow = watch('similarity_weight');
    const wFrom = profile.similarity_weight ?? 0;
    changes.push({
      label: 'Semantic weight',
      from: wFrom === 0 ? 'Off' : wFrom.toFixed(1),
      to: wNow === 0 ? 'Off' : wNow.toFixed(1),
    });
  }
  if (formState.dirtyFields.salary_floor_usd) {
    changes.push({
      label: 'Salary floor',
      from: fmtUsd(profile.salary_floor_usd),
      to: fmtUsd(watch('salary_floor_usd')),
    });
  }
  if (formState.dirtyFields.salary_ceiling_usd) {
    const ceilingNow = watch('salary_ceiling_usd');
    changes.push({
      label: 'Salary ceiling',
      from: profile.salary_ceiling_usd ? fmtUsd(profile.salary_ceiling_usd) : '—',
      to: ceilingNow > 0 ? fmtUsd(ceilingNow) : '—',
    });
  }
  if (formState.dirtyFields.seniority_levels_included) {
    const fromList = profile.seniority_levels_included ?? [];
    const toList = watch('seniority_levels_included');
    changes.push({
      label: 'Seniority levels',
      from: fromList.length ? fromList.join(', ') : '—',
      to: toList.length ? toList.join(', ') : '—',
    });
  }
  if (formState.dirtyFields.staffing_firm_blocklist) {
    const fromLen = profile.staffing_firm_blocklist.length;
    const toLen = watch('staffing_firm_blocklist')
      .split('\n')
      .filter((s) => s.trim().length > 0).length;
    changes.push({
      label: 'Staffing firm blocklist',
      from: `${fromLen} ${fromLen === 1 ? 'entry' : 'entries'}`,
      to: `${toLen} ${toLen === 1 ? 'entry' : 'entries'}`,
    });
  }

  return (
    <SettingsSection
      heading="Hard rule thresholds"
      description="Filters postings with disclosed salary, location, or applicant count outside your thresholds. Postings without disclosed salary (most listings) are always kept."
    >
      <form onSubmit={handleSubmit(onSaveClick)} className="flex flex-col gap-8">
        <SettingsRow
          label="Maximum applicant count"
          sub="Drop postings above this applicant count."
        >
          <Controller
            control={control}
            name="applicant_cap"
            render={({ field }) => (
              <SliderRow
                value={field.value}
                onChange={field.onChange}
                min={50}
                max={1000}
                step={10}
                inputAriaLabel="Maximum applicant count"
              />
            )}
          />
        </SettingsRow>

        <SettingsRow
          label="Roles per company"
          sub="How many of each company's best-fit roles to surface in lists. Set to 0 for unlimited (show every role). Raise it to see more per company."
        >
          <Controller
            control={control}
            name="per_company_cap"
            render={({ field }) => (
              <SliderRow
                value={field.value}
                onChange={field.onChange}
                min={0}
                max={25}
                step={1}
                inputAriaLabel="Roles per company"
                displayFormat={(n) => (n === 0 ? 'Unlimited' : String(n))}
              />
            )}
          />
        </SettingsRow>

        <SettingsRow
          label="Semantic weight"
          sub="How much the 'Best fit (semantic)' sort blends calibrated semantic similarity with the heuristic fit score. 0 = off (heuristic only). Higher leans on semantic similarity. Does not affect any other sort."
        >
          <Controller
            control={control}
            name="similarity_weight"
            render={({ field }) => (
              <SliderRow
                value={field.value}
                onChange={field.onChange}
                min={0}
                max={1}
                step={0.1}
                inputAriaLabel="Semantic weight"
                displayFormat={(n) => (n === 0 ? 'Off' : n.toFixed(1))}
              />
            )}
          />
        </SettingsRow>

        <SettingsRow
          label="Salary floor (annual USD)"
          sub="Hide postings whose disclosed max salary is below this. Postings without a listed salary are kept."
        >
          <Controller
            control={control}
            name="salary_floor_usd"
            render={({ field }) => (
              <SliderRow
                value={field.value}
                onChange={field.onChange}
                min={50_000}
                max={300_000}
                step={5_000}
                inputAriaLabel="Salary floor"
                displayFormat={fmtUsd}
              />
            )}
          />
        </SettingsRow>

        <SettingsRow
          label="Salary ceiling (annual USD)"
          sub="Hide postings whose disclosed min salary exceeds this. Set to 0 to disable. Postings without a listed salary are kept."
        >
          <Controller
            control={control}
            name="salary_ceiling_usd"
            render={({ field }) => (
              <SliderRow
                value={field.value}
                onChange={field.onChange}
                min={0}
                max={500_000}
                step={5_000}
                inputAriaLabel="Salary ceiling"
                displayFormat={(n) => (n > 0 ? fmtUsd(n) : 'No ceiling')}
              />
            )}
          />
        </SettingsRow>

        <SettingsRow
          label="Seniority levels to include"
          sub="Drop postings outside these levels. Leave empty to include all."
        >
          <Controller
            control={control}
            name="seniority_levels_included"
            render={({ field }) => <SeniorityChips value={field.value} onChange={field.onChange} />}
          />
        </SettingsRow>

        <SettingsRow label="Closed channels" sub="Companies you've explicitly opted out of.">
          <div className="flex flex-col gap-2">
            <ul className="flex list-none flex-col gap-1 p-0">
              {CLOSED_CHANNELS_STUB.map((row) => (
                <li
                  key={row.company}
                  className="flex items-center justify-between rounded border border-border bg-card px-3 py-2 text-[13px]"
                >
                  <span className="font-medium">{row.company}</span>
                  <span className="text-muted-foreground">{row.reason}</span>
                  <span className="font-mono text-[11px] text-muted-foreground">{row.date}</span>
                </li>
              ))}
            </ul>
            <p className="text-[12px] italic text-muted-foreground">
              Add or remove via SQL for now.
            </p>
          </div>
        </SettingsRow>

        <SettingsRow label="Staffing firm blocklist" sub="one firm per line">
          <textarea
            {...register('staffing_firm_blocklist')}
            aria-label="Staffing firm blocklist"
            className="min-h-[100px] w-full rounded-md border border-border bg-input px-3 py-2 text-[13px] outline-none placeholder:text-muted-foreground focus:border-border-strong"
          />
        </SettingsRow>

        <SettingsRow label="Role family weights" sub="0.0 = never surface · 1.0 = full weight">
          <div className="flex flex-col gap-3">
            {(Object.keys(FAMILY_LABELS) as (keyof RoleFamilyWeights)[]).map((family) => (
              <Controller
                key={family}
                control={control}
                name={`role_family_weights.${family}`}
                render={({ field }) => (
                  <div className="flex items-center gap-3 text-[13px]">
                    <span className="w-44">{FAMILY_LABELS[family]}</span>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={field.value}
                      aria-label={`${FAMILY_LABELS[family]} weight`}
                      onChange={(e) => field.onChange(Number.parseFloat(e.target.value))}
                      className="h-1 w-48 cursor-pointer accent-primary"
                    />
                    <span className="w-12 text-right font-mono text-[12px]">
                      {field.value.toFixed(2)}
                    </span>
                  </div>
                )}
              />
            ))}
            <p className="text-[12px] text-muted-foreground">
              How aggressively to surface each role family.
            </p>
          </div>
        </SettingsRow>

        <div className="flex items-center justify-end">
          <button
            type="submit"
            data-dirty={isDirty}
            className={cn(
              'inline-flex h-9 items-center rounded-md border px-4 text-sm transition-colors',
              isDirty
                ? 'border-primary bg-primary/15 text-primary hover:bg-primary/25'
                : 'border-border bg-surface text-foreground/60 hover:bg-accent',
            )}
          >
            Save hard rules
          </button>
        </div>
      </form>

      <ConfirmRulesModal
        open={modalOpen}
        onOpenChange={setModalOpen}
        changes={changes}
        onSave={onConfirmSave}
        isSaving={update.isPending}
        error={update.error ? (update.error as Error).message : null}
      />
    </SettingsSection>
  );
}

function SliderRow({
  value,
  onChange,
  min,
  max,
  step,
  inputAriaLabel,
  displayFormat,
}: {
  value: number;
  onChange: (n: number) => void;
  min: number;
  max: number;
  step: number;
  inputAriaLabel: string;
  displayFormat?: (n: number) => string;
}) {
  return (
    <div className="flex items-center gap-3">
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        aria-label={inputAriaLabel}
        onChange={(e) => {
          // Number (not parseInt) so fractional steps work (e.g. similarity
          // weight 0.1); empty/NaN falls back to min.
          const n = Number(e.target.value);
          onChange(Number.isNaN(n) ? min : n);
        }}
        className="h-8 w-28 rounded-md border border-border bg-input px-2 text-[13px] outline-none focus:border-border-strong"
      />
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        aria-label={`${inputAriaLabel} slider`}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-1 w-64 cursor-pointer accent-primary"
      />
      {displayFormat && (
        <span className="font-mono text-[12px] text-foreground">{displayFormat(value)}</span>
      )}
    </div>
  );
}

function fmtUsd(n: number): string {
  if (n >= 1000) return `$${Math.round(n / 1000)}K`;
  return `$${n}`;
}

/**
 * Multi-select chip group for the SeniorityLevel filter (PR #43).
 *
 * Click toggles inclusion. Empty selection (length 0) is the
 * "no filter applied" state — surfaced as a muted footnote so the
 * operator isn't confused by an apparently-empty control.
 */
function SeniorityChips({
  value,
  onChange,
}: {
  value: string[];
  onChange: (next: string[]) => void;
}) {
  const toggle = (level: string) => {
    if (value.includes(level)) {
      onChange(value.filter((v) => v !== level));
    } else {
      // Preserve canonical order from the SENIORITY_LEVELS constant so the
      // dirty-diff output is stable across click order.
      const next = SENIORITY_LEVELS.filter(
        (entry) => entry.value === level || value.includes(entry.value),
      ).map((entry) => entry.value);
      onChange(next);
    }
  };

  return (
    <fieldset className="flex flex-col gap-1.5 border-0 p-0">
      <legend className="sr-only">Seniority levels</legend>
      <div className="flex flex-wrap gap-1.5">
        {SENIORITY_LEVELS.map((entry) => {
          const selected = value.includes(entry.value);
          return (
            <button
              key={entry.value}
              type="button"
              aria-pressed={selected}
              onClick={() => toggle(entry.value)}
              className={cn(
                'inline-flex h-7 items-center rounded-md border px-2.5 text-[12px] transition-colors',
                selected
                  ? 'border-primary bg-primary/15 text-primary hover:bg-primary/25'
                  : 'border-border bg-surface text-foreground/80 hover:bg-accent',
              )}
            >
              {entry.label}
            </button>
          );
        })}
      </div>
      {value.length === 0 && (
        <p className="text-[11px] italic text-muted-foreground">
          All seniority levels are currently included.
        </p>
      )}
    </fieldset>
  );
}
