// Group dictation entries under date headers: Today / Yesterday / "May 28, 2026".
import type { Entry } from "./api";

export interface DateGroup {
  label: string;
  entries: Entry[];
}

function dayKey(d: Date): string {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

function labelFor(d: Date): string {
  const now = new Date();
  if (dayKey(d) === dayKey(now)) return "Today";
  const y = new Date(now);
  y.setDate(now.getDate() - 1);
  if (dayKey(d) === dayKey(y)) return "Yesterday";
  return d.toLocaleDateString(undefined, {
    month: "long",
    day: "numeric",
    year: d.getFullYear() === now.getFullYear() ? undefined : "numeric",
  });
}

/** Entries arrive newest-first; keep that order within and across groups. */
export function groupByDate(entries: Entry[]): DateGroup[] {
  const groups: DateGroup[] = [];
  let currentKey = "";
  for (const e of entries) {
    const d = new Date(e.ts);
    const key = isNaN(d.getTime()) ? "unknown" : dayKey(d);
    if (key !== currentKey) {
      currentKey = key;
      groups.push({
        label: isNaN(d.getTime()) ? "Earlier" : labelFor(d),
        entries: [e],
      });
    } else {
      groups[groups.length - 1].entries.push(e);
    }
  }
  return groups;
}
