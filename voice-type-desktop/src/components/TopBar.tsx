import { useState } from "react";
import { type Status, resetKeyboard } from "../lib/api";
// Inline the SVG markup (?raw) rather than an <img src>: the webview composites
// it as live vector geometry, so it stays crisp when downscaled to header size
// and under the app's `zoom`. An <img> SVG gets rasterized then bilinear-scaled
// (~8.5×), which looked grainy.
import lockupLight from "../assets/quobi-lockup.svg?raw";
import lockupDark from "../assets/quobi-lockup-dark.svg?raw";

export type Tab = "history" | "personalize" | "settings";

const TABS: { id: Tab; label: string }[] = [
  { id: "history", label: "History" },
  { id: "personalize", label: "Personalize" },
  { id: "settings", label: "Settings" },
];

export function TopBar({
  tab,
  setTab,
  status,
  theme,
}: {
  tab: Tab;
  setTab: (t: Tab) => void;
  status: Status | null;
  theme: "light" | "dark";
}) {
  const running = status?.daemon_running ?? false;
  const [resetMsg, setResetMsg] = useState("");
  async function handleReset() {
    setResetMsg("…");
    try {
      await resetKeyboard();
      setResetMsg("✓");
    } catch {
      setResetMsg("✕");
    }
    setTimeout(() => setResetMsg(""), 2000);
  }
  return (
    <header className="relative z-10 px-5 pt-5">
      <div className="flex items-center justify-between">
        {/* Safe: the markup is a static, build-time `?raw` import of our own
            bundled SVG — no user/runtime input, so no XSS surface. */}
        <h1
          aria-label="Quobi"
          className="flex items-center [&>svg]:h-6 [&>svg]:w-auto [&>svg]:select-none"
          dangerouslySetInnerHTML={{ __html: theme === "dark" ? lockupDark : lockupLight }}
        />
        <div className="flex items-center gap-3">
          <button
            onClick={handleReset}
            className="font-mono text-[10px] text-fg-faint transition-colors hover:text-fg"
            title="release any stuck key — recovery if the keyboard ever gets grabbed"
          >
            ⟳ reset keys{resetMsg && ` ${resetMsg}`}
          </button>
          <span
            className="flex items-center gap-1.5 font-mono text-[10px] text-fg-soft"
            title={running ? "daemon is running. Press your hotkey to dictate." : "daemon is not running"}
          >
            <span className={`inline-block h-1.5 w-1.5 rounded-full ${running ? "bg-accent pulse" : "bg-fg-faint"}`} />
            {running ? "ready" : "off"}
          </span>
        </div>
      </div>

      {/* segmented tabs */}
      <nav className="mt-4 flex gap-1 rounded-lg bg-bg-soft p-1">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            style={tab === t.id ? { boxShadow: "inset 0 -2px 0 var(--accent)" } : undefined}
            className={[
              "flex-1 rounded-md py-1.5 text-[12px] font-medium transition-colors",
              tab === t.id ? "bg-surface text-fg" : "text-fg-soft hover:text-fg",
            ].join(" ")}
          >
            {t.label}
          </button>
        ))}
      </nav>
    </header>
  );
}
