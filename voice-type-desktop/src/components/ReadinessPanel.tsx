import { useCallback, useEffect, useState } from "react";
import {
  type Readiness, type ReadinessProblem,
  getReadiness, fixInputGroup, startYdotoold, installPackages,
  restartDaemon, copyText,
} from "../lib/api";

/**
 * System-readiness panel. Surfaces the fresh-install environment problems the
 * daemon otherwise only records in a log the user never opens — not in the
 * `input` group, ydotoold not running, a missing runtime package — and offers a
 * one-click fix for each (a single pkexec prompt where root is needed). Renders
 * nothing when the system is healthy, so it never nags. Sibling of SetupBanner.
 */

// Visual treatment per severity, reusing the app's palette tokens.
const SEV: Record<ReadinessProblem["severity"], { dot: string; ring: string; icon: string }> = {
  error:   { dot: "bg-red-500",    ring: "border-red-500/30",    icon: "!" },
  warn:    { dot: "bg-amber-500",  ring: "border-amber-500/30",  icon: "!" },
  relogin: { dot: "bg-accent",     ring: "border-accent/30",     icon: "↻" },
};

export function ReadinessPanel({ onChanged }: { onChanged?: () => void }) {
  const [rd, setRd] = useState<Readiness | null>(null);
  const [busy, setBusy] = useState<string>("");   // code of the problem being fixed
  const [note, setNote] = useState<Record<string, string>>({});

  const refresh = useCallback(async () => {
    try {
      setRd(await getReadiness());
    } catch (e) {
      console.error("readiness check failed", e);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 8000); // slower than the 4s status poll — env rarely changes
    return () => clearInterval(t);
  }, [refresh]);

  const setProblemNote = (code: string, msg: string) =>
    setNote((n) => ({ ...n, [code]: msg }));

  async function runFix(p: ReadinessProblem) {
    setBusy(p.code);
    setProblemNote(p.code, "");
    try {
      if (p.fix === "input_group") {
        await fixInputGroup();
        setProblemNote(p.code, "done — now log out and back in");
      } else if (p.fix === "ydotoold") {
        await startYdotoold();
        await restartDaemon();
      } else if (p.fix.startsWith("install:")) {
        const pkgs = p.fix.slice("install:".length).split(",").filter(Boolean);
        await installPackages(pkgs);
        await restartDaemon();
      } else if (p.fix.startsWith("copy:")) {
        await copyText(p.fix.slice("copy:".length));
        setProblemNote(p.code, "copied — run it in a terminal");
      }
      await refresh();
      onChanged?.();
    } catch (e) {
      setProblemNote(p.code, e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  // Button label + whether the fix is actionable in-app.
  function action(p: ReadinessProblem): { label: string } | null {
    if (p.fix === "input_group") return { label: "Fix this" };
    if (p.fix === "ydotoold") return { label: "Start it" };
    if (p.fix.startsWith("install:")) return { label: "Install" };
    if (p.fix.startsWith("copy:")) return { label: "Copy command" };
    return null; // relogin / daemon-only messages have no button
  }

  if (!rd || rd.problems.length === 0) return null;

  return (
    <div className="border-b border-line bg-surface/70">
      {rd.problems.map((p) => {
        const sev = SEV[p.severity];
        const act = action(p);
        const isBusy = busy === p.code;
        const msg = note[p.code];
        // For a copy-only fix, also show the raw command so it's visible.
        const cmd = p.fix.startsWith("copy:") ? p.fix.slice("copy:".length) : "";
        return (
          <div
            key={p.code}
            className={`flex items-start gap-3 border-l-2 ${sev.ring} px-5 py-2.5`}
          >
            <span
              className={`mt-1 inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full ${sev.dot} text-[9px] font-bold leading-none text-white`}
              aria-hidden
            >
              {sev.icon}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-[12px] font-medium text-fg">{p.title}</p>
              <p className="text-[11px] leading-snug text-fg-soft">{p.detail}</p>
              {cmd && (
                <code className="mt-1 block truncate rounded bg-line/60 px-1.5 py-0.5 font-mono text-[10px] text-fg-soft">
                  {cmd}
                </code>
              )}
              {msg && (
                <p className="mt-1 font-mono text-[10px] text-fg-faint">{msg}</p>
              )}
            </div>
            {act && (
              <button
                onClick={() => runFix(p)}
                disabled={isBusy}
                className="mt-0.5 shrink-0 rounded-md bg-accent px-3 py-1.5 text-[11px] font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                {isBusy ? "working…" : act.label}
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
