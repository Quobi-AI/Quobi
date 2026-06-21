import { AnimatePresence } from "motion/react";
import { type Entry } from "../lib/api";
import { groupByDate } from "../lib/group";
import { computeMetrics, formatNumber } from "../lib/metrics";
import { DictationEntry } from "./DictationCard";

export function HistoryView({
  entries,
  loading,
}: {
  entries: Entry[];
  loading: boolean;
}) {
  const groups = groupByDate(entries);
  const { words, minutesSaved } = computeMetrics(entries);
  let i = 0;

  return (
    <div className="scroll-region flex-1 overflow-y-auto px-2 pb-8">
      {/* words-saved metric — the product's hook */}
      <div className="mx-1 mt-3 mb-1 rounded-xl bg-bg-soft px-4 py-4">
        <div className="flex items-baseline gap-2">
          <span className="text-[34px] font-semibold leading-none tracking-tight text-accent tabular-nums">
            {formatNumber(words)}
          </span>
          <span className="text-[14px] text-fg-soft">words spoken</span>
        </div>
        <p className="mt-1.5 font-mono text-[11px] text-fg-faint">
          {minutesSaved >= 1
            ? `≈ ${formatNumber(Math.round(minutesSaved))} min saved vs typing`
            : "your words, cleaned and set"}
        </p>
      </div>

      {loading ? (
        <Note>…</Note>
      ) : entries.length === 0 ? (
        <Note>
          <span className="font-hand text-[24px] italic text-fg">Nothing yet.</span>
          <span className="mt-2 block font-mono text-[11px] text-fg-faint">
            Hold your hotkey and speak.
          </span>
        </Note>
      ) : (
        groups.map((g) => (
          <section key={g.label}>
            <div className="date-sticky px-3 pb-1 pt-4">
              <h2 className="font-hand text-[19px] italic leading-none text-fg">{g.label}</h2>
            </div>
            <AnimatePresence initial={false}>
              {g.entries.map((e) => (
                <DictationEntry key={e.id || i} entry={e} index={i++} />
              ))}
            </AnimatePresence>
          </section>
        ))
      )}
    </div>
  );
}

function Note({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-64 flex-col items-center justify-center px-10 text-center text-[13px] text-fg-soft">
      {children}
    </div>
  );
}
