// Word-level diff between the raw transcript and the cleaned result, so the
// UI can render the raw text with removed words (filler, false starts) struck
// through in red — showing the edit the cleanup made.

export interface Token {
  text: string;
  removed: boolean;
}

function tokenize(s: string): string[] {
  // keep words and the whitespace/punctuation between them as separate tokens
  return s.match(/\s+|[^\s]+/g) ?? [];
}

function norm(t: string): string {
  return t.toLowerCase().replace(/[^\p{L}\p{N}']/gu, "");
}

/**
 * Returns the RAW tokens, each flagged removed=true if it isn't part of the
 * longest common subsequence with the cleaned text. Whitespace tokens inherit
 * the removed state of the word that follows, so struck runs read naturally.
 */
export function markupRemovals(raw: string, cleaned: string): Token[] {
  const rawTokens = tokenize(raw);
  const cleanTokens = tokenize(cleaned).filter((t) => t.trim().length > 0);

  // words only (skip whitespace) for the LCS, but remember original indices
  const rawWords: { tok: string; idx: number }[] = [];
  rawTokens.forEach((tok, idx) => {
    if (tok.trim().length > 0) rawWords.push({ tok, idx });
  });

  // LCS over normalized words
  const a = rawWords.map((w) => norm(w.tok));
  const b = cleanTokens.map(norm);
  const m = a.length;
  const n = b.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  // backtrack: mark which raw words are kept
  const kept = new Set<number>();
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (a[i] === b[j]) {
      kept.add(rawWords[i].idx);
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      i++;
    } else {
      j++;
    }
  }

  const removedIdx = new Set<number>();
  rawWords.forEach((w) => {
    if (!kept.has(w.idx)) removedIdx.add(w.idx);
  });

  // build output tokens; merge adjacent same-state into runs for clean strikes
  const out: Token[] = [];
  rawTokens.forEach((tok, idx) => {
    const isWord = tok.trim().length > 0;
    const removed = isWord ? removedIdx.has(idx) : false;
    const prev = out[out.length - 1];
    if (prev && prev.removed === removed) {
      prev.text += tok;
    } else {
      out.push({ text: tok, removed });
    }
  });
  return out;
}
