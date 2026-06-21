// "Report a bug" → opens a pre-filled GitHub issue in the user's browser.
//
// No API/token: we just open github.com/<repo>/issues/new with the title + body
// pre-filled (and auto-attached diagnostics). The user reviews and hits Submit
// (a GitHub account is required to submit — that's GitHub's side, not ours).
import { getStatus, type Entry } from "./api";

// ⚠️ SET THIS to your GitHub repo (owner/name) so the bug links open issues
// there. Until it's set, the report buttons stay disabled (see isReportConfigured).
export const GITHUB_REPO: string = "Quobi-AI/Quobi";

export const isReportConfigured = (): boolean =>
  GITHUB_REPO !== "OWNER/REPO" && GITHUB_REPO.includes("/");

function trunc(s: string, n = 500): string {
  s = (s || "").trim();
  return s.length > n ? s.slice(0, n) + "…" : s;
}

/** Build a pre-filled GitHub new-issue URL, optionally about a specific entry. */
export async function buildIssueUrl(entry?: Entry): Promise<string> {
  let diag = "_unavailable_";
  try {
    const s = await getStatus();
    diag = [
      `- session: ${s.session}`,
      `- cleanup model: ${s.model} (cleanup ${s.cleanup_enabled ? "on" : "off"})`,
      `- output: ${s.output_mode}`,
      `- hotkey: ${s.hotkey} (${s.hotkey_mode})`,
    ].join("\n");
  } catch { /* diagnostics are best-effort */ }
  try {
    const { getVersion } = await import("@tauri-apps/api/app");
    diag = `- app version: ${await getVersion()}\n${diag}`;
  } catch { /* not in Tauri, or app plugin missing */ }

  const title = entry
    ? `Dictation issue: ${trunc(entry.cleaned || entry.raw || "(silence)", 50)}`
    : "Bug report";

  const lines = ["**What went wrong?**", "", "<!-- describe the problem here -->", ""];
  if (entry) {
    lines.push("**The dictation**");
    if (entry.status === "failed") lines.push(`- error: \`${entry.error}\``);
    lines.push(`- raw (Whisper): ${trunc(entry.raw) || "_(none)_"}`);
    lines.push(`- cleaned (output): ${trunc(entry.cleaned) || "_(none)_"}`);
    if (entry.duration > 0) lines.push(`- duration: ${entry.duration.toFixed(1)}s`);
    lines.push("");
  }
  lines.push("<details><summary>Diagnostics</summary>", "", diag, "</details>");

  const q = new URLSearchParams({
    title,
    body: lines.join("\n"),
    labels: "bug",
  });
  return `https://github.com/${GITHUB_REPO}/issues/new?${q.toString()}`;
}

/** Open the pre-filled GitHub issue in the default browser. */
export async function openIssue(entry?: Entry): Promise<void> {
  const url = await buildIssueUrl(entry);
  try {
    const { openUrl } = await import("@tauri-apps/plugin-opener");
    await openUrl(url);
  } catch {
    window.open(url, "_blank", "noopener");
  }
}
