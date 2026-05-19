import { AppShell } from '@/components/chrome/AppShell';
import { PlaceholderPage } from '@/components/chrome/PlaceholderPage';
import { ThemeToggle } from '@/components/chrome/ThemeToggle';

/**
 * Settings page placeholder (#32a).
 *
 * Hosts the working `<ThemeToggle />` in the Appearance section as
 * the only real interactive control before #32c lands the full
 * Settings page (Profile · Hard rules · API keys · Manual jobs).
 */
export default function SettingsPage() {
  return (
    <AppShell title="Settings" subtitle="Appearance · Profile · Rules">
      <PlaceholderPage
        heading="Settings page — coming in #32c"
        body="Profile editor · Hard-rule thresholds · API keys · Manual job triggers. The Appearance section above is wired in #32a so the theme toggle is usable today."
        extra={
          <div className="flex flex-col gap-3">
            <h2 className="text-sm font-semibold">Appearance</h2>
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">
                Theme — Light · warm off-white default
              </p>
              <ThemeToggle />
            </div>
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">UI scale — coming in #32c</p>
              <input
                type="range"
                min={80}
                max={120}
                defaultValue={100}
                aria-label="UI scale (stub)"
                disabled
                className="h-2 w-48 cursor-not-allowed opacity-50"
              />
            </div>
          </div>
        }
      />
    </AppShell>
  );
}
