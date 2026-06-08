import { useState } from "react";
import { type Status, startDaemon, resetKeyboard } from "../lib/api";

export function StatusPanel({
  status,
  theme,
  onToggleTheme,
  onChanged,
}: {
  status: Status | null;
  theme: "light" | "dark";
  onToggleTheme: () => void;
  onChanged: () => void;
}) {
  const running = status?.daemon_running ?? false;
  const [resetMsg, setResetMsg] = useState("");

  async function handleStart() {
    await startDaemon();
    setTimeout(onChanged, 1400);
  }

  async function handleReset() {
    setResetMsg("releasing…");
    try {
      await resetKeyboard();
      setResetMsg("✓ keys released");
    } catch {
      setResetMsg("failed — is ydotool installed?");
    }
    setTimeout(() => setResetMsg(""), 2500);
  }

  return (
    <header className="relative z-10 px-5 pt-6 pb-3">
      <div className="flex items-center justify-between">
        <h1 className="text-[19px] font-semibold tracking-tight text-fg">
          Quo<span className="text-accent">bi</span>
        </h1>
        <button
          onClick={onToggleTheme}
          className="font-mono text-[11px] lowercase text-fg-faint transition-colors hover:text-fg"
          title="toggle light / dark"
        >
          {theme === "light" ? "◑ dark" : "◐ light"}
        </button>
      </div>

      {/* one slim status line */}
      <div className="mt-2 flex items-center gap-2 font-mono text-[11px] text-fg-soft">
        <span className={`inline-block h-1.5 w-1.5 rounded-full ${running ? "bg-accent pulse" : "bg-fg-faint"}`} />
        <span>{running ? "listening" : "asleep"}</span>
        {status && (
          <>
            <span className="text-fg-faint">·</span>
            <span className="text-fg-faint">{status.hotkey}</span>
          </>
        )}
        <span className="ml-auto flex items-center gap-3">
          {resetMsg && <span className="text-fg-faint">{resetMsg}</span>}
          <button
            onClick={handleReset}
            className="text-fg-faint transition-colors hover:text-fg"
            title="release any stuck key — recovery if the keyboard gets grabbed"
          >
            ⟳ reset keys
          </button>
          {!running && (
            <button
              onClick={handleStart}
              className="text-accent transition-opacity hover:opacity-70"
            >
              wake →
            </button>
          )}
        </span>
      </div>
    </header>
  );
}
