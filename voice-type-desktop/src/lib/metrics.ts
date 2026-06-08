import type { Entry } from "./api";

function countWords(s: string): number {
  const t = s.trim();
  if (!t) return 0;
  return t.split(/\s+/).length;
}

export interface Metrics {
  words: number;
  takes: number;
  minutesSaved: number; // vs typing at ~38 wpm, minus speaking time
}

export function computeMetrics(entries: Entry[]): Metrics {
  let words = 0;
  let takes = 0;
  let spokenSec = 0;
  for (const e of entries) {
    if (e.status !== "ok" || !e.cleaned) continue;
    words += countWords(e.cleaned);
    takes += 1;
    spokenSec += e.duration || 0;
  }
  // typing 38 wpm avg → seconds to type; subtract the time actually spent speaking
  const typeSec = (words / 38) * 60;
  const minutesSaved = Math.max(0, (typeSec - spokenSec) / 60);
  return { words, takes, minutesSaved };
}

export function formatNumber(n: number): string {
  return n.toLocaleString();
}
