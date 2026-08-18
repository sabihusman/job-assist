'use client';

import { Controller, useForm } from 'react-hook-form';
import { toast } from 'sonner';

import { SettingsRow, SettingsSection } from '@/components/settings/layout';
import { TagInput } from '@/components/shared/TagInput';
import { useUpdateProfile } from '@/lib/api/settings';
import type { OperatorProfileRead, OperatorProfileUpdate } from '@/lib/settings/types';

/**
 * Profile section — looking-for free-form text + tag lists. The
 * backend schema has no `name` field today (PR #32c audit); the spec
 * mentions Name but it's stripped here per the v1 cuts.
 *
 * Save button stays in idle appearance regardless of dirty state
 * (per spec). On click, PUT /operator/profile with the full form
 * state. Toast on success; inline red error on failure.
 */

type ProfileFormState = {
  looking_for_text: string;
  role_keywords: string[];
  geo_whitelist: string[];
};

export function ProfileSection({ profile }: { profile: OperatorProfileRead }) {
  const { handleSubmit, control, register, formState } = useForm<ProfileFormState>({
    defaultValues: {
      looking_for_text: profile.looking_for_text,
      role_keywords: profile.role_keywords,
      geo_whitelist: profile.geo_whitelist,
    },
  });
  const update = useUpdateProfile();

  const onSubmit = async (values: ProfileFormState) => {
    const body: OperatorProfileUpdate = {
      looking_for_text: values.looking_for_text,
      role_keywords: values.role_keywords,
      geo_whitelist: values.geo_whitelist,
    };
    try {
      const saved = await update.mutateAsync(body);
      // D1 (D-SETTINGS-REWIRE): report the embedding side effect accurately —
      // the vector is rewritten only when looking_for_text actually changed,
      // and the backend now surfaces embed/rescore failures instead of
      // swallowing them under an unconditional "vector rewritten" toast.
      if (saved.embed_status === 'failed') {
        toast.error(
          'Profile saved, but re-embedding failed — semantic scores are stale. Save again to retry.',
        );
      } else if (saved.rescore_ok === false) {
        toast.warning(
          'Profile saved, but the follow-up rescore failed — fit scores may lag until the next sweep.',
        );
      } else if (saved.embed_status === 'rewritten') {
        toast.success('✓ Profile saved · semantic vector updated');
      } else {
        toast.success('✓ Profile saved');
      }
    } catch {
      // Error message rendered inline below by reading `update.error`.
    }
  };

  return (
    <SettingsSection
      heading="Profile"
      description="Identity, scope, and the free-form signal that drives scoring."
    >
      <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-6">
        <SettingsRow label="Current role keywords" sub="press Enter to add">
          <Controller
            control={control}
            name="role_keywords"
            render={({ field }) => (
              <TagInput
                value={field.value}
                onChange={field.onChange}
                placeholder="add keyword…"
                inputAriaLabel="Add role keyword"
              />
            )}
          />
        </SettingsRow>

        <SettingsRow label="Geography whitelist" sub="press Enter to add">
          <Controller
            control={control}
            name="geo_whitelist"
            render={({ field }) => (
              <TagInput
                value={field.value}
                onChange={field.onChange}
                placeholder="add location…"
                inputAriaLabel="Add location"
              />
            )}
          />
        </SettingsRow>

        <SettingsRow
          label="What I'm looking for right now"
          sub="free-form — feeds semantic matching and the classifier"
        >
          <div className="flex flex-col gap-2">
            <textarea
              {...register('looking_for_text')}
              aria-label="What I'm looking for right now"
              className="min-h-[140px] w-full rounded-md border border-border bg-input px-3 py-2 text-[13px] outline-none placeholder:text-muted-foreground focus:border-border-strong"
            />
            <p className="text-[12px] text-muted-foreground">
              Feeds the semantic-fit slice of scoring (20%, alongside role family and seniority) and
              steers the LLM classifier. Rewrite anytime your preferences shift.
            </p>
          </div>
        </SettingsRow>

        <div className="flex items-center justify-end gap-3">
          {update.error && (
            <span className="text-[12px] text-negative">
              {(update.error as Error).message ?? 'Save failed'}
            </span>
          )}
          <button
            type="submit"
            disabled={update.isPending || formState.isSubmitting}
            className="inline-flex h-9 items-center rounded-md border border-border bg-surface px-4 text-sm hover:bg-accent disabled:opacity-50"
          >
            {update.isPending ? 'Saving…' : 'Save profile'}
          </button>
        </div>
      </form>
    </SettingsSection>
  );
}
