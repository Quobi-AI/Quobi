import { useMemo, useState } from "react";
import { motion } from "motion/react";
import { type Entry, copyText, retry } from "../lib/api";
import { markupRemovals } from "../lib/diff";
import { openIssue, isReportConfigured } from "../lib/report";

function clockTime(ts: string): string {
  const d = new Date(ts);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

export function DictationEntry({
  entry,
  index,
  onUpdated,
}: {
  entry: Entry;
  index: number;
  onUpdated: (e: Entry) => void;
}) {
  const [copied, setCopied] = useState(false);
  const [showEdits, setShowEdits] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [err, setErr] = useState("");
  const [confirmReport, setConfirmReport] = useState(false);

  const failed = entry.status === "failed";
  const hasAudio = entry.audio.length > 0;
  const hasEdits = entry.raw && entry.cleaned && entry.raw !== entry.cleaned;

  const tokens = useMemo(
    () => (hasEdits ? markupRemovals(entry.raw, entry.cleaned) : []),
    [entry.raw, entry.cleaned, hasEdits],
  );

  async function doCopy() {
    await copyText(entry.cleaned);
    setCopied(true);
    setTimeout(() => setCopied(false), 1100);
  }
  async function doRetry() {
    setRetrying(true);
    setErr("");
    try {
      onUpdated(await retry(entry.id, entry.audio));
    } catch (e) {
      setErr(String(e));
    } finally {
      setRetrying(false);
    }
  }
  async function doReport() {
    setConfirmReport(false);
    await openIssue(entry); // opens a pre-filled GitHub issue with this entry's context
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.26, delay: Math.min(index * 0.02, 0.2), ease: [0.16, 1, 0.3, 1] }}
      className="group flex gap-3 rounded-lg px-3 py-2.5 transition-colors hover:bg-bg-soft"
    >
      {/* time gutter */}
      <span className="mt-1 w-12 shrink-0 select-text text-right font-mono text-[10px] text-fg-faint">
        {clockTime(entry.ts)}
      </span>

      <div className="min-w-0 flex-1">
        {failed ? (
          <p className="text-[15px] leading-snug text-accent">
            Couldn't transcribe
            <span className="ml-1 font-mono text-[11px] text-fg-faint">
              · {entry.error || "error"}
            </span>
          </p>
        ) : showEdits && hasEdits ? (
          <p className="select-text text-[15.5px] leading-relaxed text-fg">
            {tokens.map((t, i) =>
              t.removed ? <span key={i} className="struck">{t.text}</span> : <span key={i}>{t.text}</span>,
            )}
          </p>
        ) : (
          <p className="select-text text-[15.5px] leading-relaxed text-fg">
            {entry.cleaned || <span className="italic text-fg-faint">silence</span>}
          </p>
        )}

        {/* actions — only visible on hover, ultra-minimal */}
        <div className="mt-1 flex items-center gap-3.5 opacity-0 transition-opacity group-hover:opacity-100">
          {entry.cleaned && (
            <Action onClick={doCopy} active={copied}>{copied ? "copied" : "copy"}</Action>
          )}
          {hasEdits && (
            <Action onClick={() => setShowEdits((v) => !v)} active={showEdits}>
              {showEdits ? "result" : "edits"}
            </Action>
          )}
          {hasAudio && (
            <Action onClick={doRetry} accent disabled={retrying}>
              {retrying ? "…" : "retry"}
            </Action>
          )}
          {entry.duration > 0 && (
            <span className="font-mono text-[10px] text-fg-faint">{entry.duration.toFixed(1)}s</span>
          )}
        </div>
        {err && <p className="mt-1 font-mono text-[10px] text-accent">{err}</p>}
      </div>

      {/* report — a small red "!" in the far-right corner. Hidden until you
          hover the row (like copy/edits/retry), then ask + open a pre-filled
          GitHub issue carrying this dictation's context. */}
      {isReportConfigured() && (
        <div className="shrink-0 self-start pt-0.5 opacity-0 transition-opacity group-hover:opacity-100">
          {confirmReport ? (
            <div className="flex items-center gap-1.5">
              <button
                onClick={doReport}
                className="font-mono text-[10px] text-accent hover:underline"
              >
                report ↗
              </button>
              <button
                onClick={() => setConfirmReport(false)}
                className="font-mono text-[10px] text-fg-faint transition-colors hover:text-fg"
                aria-label="cancel"
              >
                ✕
              </button>
            </div>
          ) : (
            <button
              onClick={() => setConfirmReport(true)}
              title="Report a problem with this dictation"
              className="flex h-5 w-5 items-center justify-center rounded-full text-[13px] font-bold leading-none text-accent/70 transition-colors hover:text-accent"
            >
              !
            </button>
          )}
        </div>
      )}
    </motion.div>
  );
}

function Action({
  children,
  onClick,
  active,
  accent,
  disabled,
}: {
  children: React.ReactNode;
  onClick: () => void;
  active?: boolean;
  accent?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={[
        "font-mono text-[10px] lowercase tracking-wide transition-colors disabled:opacity-40",
        accent || active ? "text-accent" : "text-fg-soft hover:text-fg",
      ].join(" ")}
    >
      {children}
    </button>
  );
}
