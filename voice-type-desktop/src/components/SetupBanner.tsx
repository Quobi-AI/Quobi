import { useEffect, useState } from "react";
import {
  isParakeetDownloaded, startParakeetDownload,
  isCleanupDownloaded, startCleanupDownload,
  downloadProgress, restartDaemon,
} from "../lib/api";

/**
 * First-run setup: pull the on-device models so Quobi works fully. The speech
 * model is NVIDIA Parakeet (runs on the CPU on every machine, 20x+ faster than
 * real-time, so no GPU is needed for speech), plus the cleanup model (Quill 2B,
 * ~1.2 GB). The daemon already runs without them (raw transcription / no polish),
 * so this is a "finish setup" nudge, not a hard blocker. Dismissible; gone once
 * both are installed. Downloads share one progress file (sequential).
 */
type Phase = "speech" | "cleanup" | null;
const CLEANUP_TIER = "2b";                  // download_cleanup_model writes model="2b"
const PARAKEET_MODEL = "parakeet-tdt-0.6b-v2"; // download_parakeet_model writes this id

// Start a download, then poll the shared progress file until it's done. We key
// on the `model` field so a stale record from the PREVIOUS download (e.g. the
// speech "done" still sitting there when we kick off the cleanup) can't make us
// resolve early.
function runDownload(
  start: () => Promise<void>,
  expectedModel: string,
  onPct: (p: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    start().then(() => {
      const timer = setInterval(async () => {
        const p = await downloadProgress();
        if (p.model !== expectedModel) return; // not our download yet (stale record)
        if (p.state === "downloading") onPct(p.pct);
        else if (p.state === "done") { clearInterval(timer); resolve(); }
        else if (p.state === "error") { clearInterval(timer); reject(new Error(p.error || "download failed")); }
      }, 500);
    }, reject);
  });
}

export function SetupBanner() {
  const [speechReady, setSpeechReady] = useState<boolean | null>(null);
  const [cleanupReady, setCleanupReady] = useState<boolean | null>(null);
  const [phase, setPhase] = useState<Phase>(null);
  const [pct, setPct] = useState(0);
  const [error, setError] = useState("");
  const [dismissed, setDismissed] = useState(
    () => localStorage.getItem("vt-setup-dismissed") === "1",
  );

  useEffect(() => {
    isParakeetDownloaded("english").then(setSpeechReady);
    isCleanupDownloaded(CLEANUP_TIER).then(setCleanupReady);
  }, []);

  async function download() {
    setError("");
    try {
      if (!speechReady) {
        setPhase("speech"); setPct(0);
        await runDownload(() => startParakeetDownload("english"), PARAKEET_MODEL, setPct);
        setSpeechReady(true);
      }
      if (!cleanupReady) {
        setPhase("cleanup"); setPct(0);
        await runDownload(() => startCleanupDownload(CLEANUP_TIER), CLEANUP_TIER, setPct);
        setCleanupReady(true);
      }
      setPhase(null);
      await restartDaemon(); // pick up the now-present models (full cleanup + GPU STT)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPhase(null);
    }
  }

  if (speechReady === null || cleanupReady === null) return null; // still checking
  if (speechReady && cleanupReady) return null;                   // all set
  if (dismissed && phase === null) return null;

  const needBoth = !speechReady && !cleanupReady;
  const sizeLabel = needBoth ? "~1.8 GB" : !speechReady ? "~650 MB" : "~1.2 GB";
  const idleDesc = needBoth
    ? "Download the speech + cleanup models"
    : !speechReady
      ? "Download the speech model"
      : "Download the cleanup model";
  const speechHint = "NVIDIA Parakeet, on-device on the CPU, no GPU needed";
  const phaseLabel = phase === "speech" ? "speech model" : "cleanup model";

  return (
    <div className="flex items-center gap-3 border-b border-line bg-surface/70 px-5 py-2.5">
      <div className="min-w-0 flex-1">
        <p className="text-[12px] font-medium text-fg">
          Finish setting up Quobi
          <span className="ml-1.5 font-mono text-[10px] text-fg-faint">{sizeLabel}</span>
        </p>
        {phase === null ? (
          <p className="truncate text-[11px] text-fg-soft">
            {idleDesc} so it runs fully on-device ({speechHint}). One-time.
          </p>
        ) : (
          <>
            <div className="mb-0.5 flex items-center justify-between text-[10px] text-fg-faint">
              <span>downloading {phaseLabel}…</span>
              <span className="font-mono tabular-nums">{pct}%</span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-line">
              <div
                className="h-full rounded-full bg-accent transition-[width] duration-300 ease-out"
                style={{ width: `${pct}%` }}
              />
            </div>
          </>
        )}
        {error && <p className="mt-1 font-mono text-[10px] text-red-500">error: {error}</p>}
      </div>
      {phase === null && (
        <>
          <button
            onClick={download}
            className="shrink-0 rounded-md bg-accent px-3 py-1.5 text-[11px] font-medium text-white transition-opacity hover:opacity-90"
          >
            Download
          </button>
          <button
            onClick={() => { setDismissed(true); localStorage.setItem("vt-setup-dismissed", "1"); }}
            className="shrink-0 text-fg-faint transition-colors hover:text-fg"
            title="dismiss"
          >
            ✕
          </button>
        </>
      )}
    </div>
  );
}
