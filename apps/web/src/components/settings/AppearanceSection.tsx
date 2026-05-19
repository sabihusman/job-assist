'use client';

import { useTheme } from 'next-themes';
import { useEffect, useState } from 'react';

import { ThemeToggle } from '@/components/chrome/ThemeToggle';
import { UIScaleControl } from '@/components/settings/UIScaleControl';
import { SettingsRow, SettingsSection } from '@/components/settings/layout';

/**
 * Appearance section — Theme toggle + UI scale. Both controls live
 * here; ThemeToggle was already authored in PR #32a and is the only
 * widget in chrome that the spec keeps Settings-only.
 */
export function AppearanceSection() {
  return (
    <SettingsSection
      heading="Appearance"
      description="Light by default. Dark mode is here when you want it."
    >
      <SettingsRow label="Theme" sub={<ThemeSubLabel />}>
        <ThemeToggle />
      </SettingsRow>
      <SettingsRow
        label="UI scale"
        sub="Scales all text, spacing, and cards. 2% steps, up to +20%."
      >
        <UIScaleControl />
      </SettingsRow>
    </SettingsSection>
  );
}

function ThemeSubLabel() {
  const { theme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  // Match the server-rendered fallback before client hydration.
  const resolved = mounted ? (theme ?? 'light') : 'light';
  return (
    <span className="text-[12px] text-muted-foreground">
      {resolved === 'dark' ? 'Dark · low-light operator mode' : 'Light · warm off-white default'}
    </span>
  );
}
