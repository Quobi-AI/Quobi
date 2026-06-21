import { useEffect, useState } from "react";
import {
  type Status, type CleanupSettings, saveHotkey, restartDaemon,
  downloadProgress,
  getCleanupSettings, saveCleanupSettings, discoverLocalModels,
  QUILL_TIERS, isCleanupDownloaded, startCleanupDownload,
  type ParakeetVariant,
  isParakeetDownloaded, startParakeetDownload,
  getParakeetVariant, setParakeetVariant,
  getAutostart, setAutostart,
} from "../lib/api";
import { KeyCapture } from "./KeyCapture";
import { openIssue, isReportConfigured } from "../lib/report";

export function SettingsView({
  status,
  theme,
  setTheme,
  size,
  setSize,
}: {
  status: Status | null;
  theme: "light" | "dark";
  setTheme: (t: "light" | "dark") => void;
  size: string;
  setSize: (s: string) => void;
}) {
  const [restarting, setRestarting] = useState(false);
  const [hotkey, setHotkey] = useState("grave");
  const [hkMode, setHkMode] = useState("hold");
  const [hkDirty, setHkDirty] = useState(false);
  const [cleanup, setCleanup] = useState<CleanupSettings>({
    local_model: "", local_accel: "auto",
  });
  const [cleanupSaved, setCleanupSaved] = useState(false);
  // .gguf files found in the models dir (the dropdown options).
  const [localModels, setLocalModels] = useState<string[]>([]);
  // Quill cleanup-model download state.
  const [downloaded, setDownloaded] = useState<Set<string>>(new Set());
  const [clTier, setClTier] = useState<string | null>(null);
  const [clPct, setClPct] = useState<number | null>(null);
  const [clError, setClError] = useState("");
  // Parakeet speech-model: which language variant + its download state.
  const [variant, setVariant] = useState<ParakeetVariant>("english");
  const [speechReady, setSpeechReady] = useState(false);
  const [sPct, setSPct] = useState<number | null>(null);
  const [sError, setSError] = useState("");
  // "Start dictation on login".
  const [autostart, setAutostartState] = useState(true);

  // Authoritative re-check of which Quill tiers are on disk — SETS the full set
  // (not just adds), so a model deleted from disk drops out and its download
  // card comes back. Run on mount and on the refresh button.
  async function refreshDownloaded() {
    const oks = await Promise.all(
      QUILL_TIERS.map((t) => isCleanupDownloaded(t.tier).then((ok) => [t.tier, ok] as const)),
    );
    setDownloaded(new Set(oks.filter(([, ok]) => ok).map(([tier]) => tier)));
  }

  useEffect(() => {
    // Cleanup always runs on-device — just the local model + acceleration.
    getCleanupSettings().then(setCleanup);
    discoverLocalModels().then(setLocalModels);
    refreshDownloaded();
    getAutostart().then(setAutostartState);
    // Load the selected language variant and check whether its model is present.
    (async () => {
      const v = await getParakeetVariant();
      setVariant(v);
      setSpeechReady(await isParakeetDownloaded(v));
    })();
  }, []);

  // Switch the language variant: point the daemon at it, refresh install state,
  // restart. (The model still needs downloading if it isn't on disk yet.)
  async function changeVariant(v: ParakeetVariant) {
    setVariant(v);
    setSError("");
    await setParakeetVariant(v);
    setSpeechReady(await isParakeetDownloaded(v));
    await restartDaemon();
  }

  // Download the selected variant's Parakeet model, then restart so the daemon
  // picks up the on-device transcription path.
  async function downloadSpeech() {
    setSError("");
    setSPct(0);
    await startParakeetDownload(variant);
    const timer = setInterval(async () => {
      const p = await downloadProgress();
      if (p.state === "downloading") setSPct(p.pct);
      else if (p.state === "done") {
        clearInterval(timer);
        setSPct(null);
        setSpeechReady(true);
        await restartDaemon();
      } else if (p.state === "error") {
        clearInterval(timer);
        setSPct(null);
        setSError(p.error || "download failed");
      }
    }, 500);
  }

  // Download a Quill cleanup tier, then auto-select it once it lands.
  async function downloadTier(tier: string) {
    setClError("");
    setClTier(tier);
    setClPct(0);
    await startCleanupDownload(tier);
    const timer = setInterval(async () => {
      const p = await downloadProgress();
      if (p.model && p.model !== tier) return; // stale record
      if (p.state === "downloading") setClPct(p.pct);
      else if (p.state === "done") {
        clearInterval(timer);
        setClPct(null);
        setClTier(null);
        setDownloaded((s) => new Set(s).add(tier));
        const models = await discoverLocalModels();
        setLocalModels(models);
        const added = models.find((m) => m.includes(`quill-${tier}-`));
        if (added) await commitCleanup({ ...cleanup, local_model: added });
      } else if (p.state === "error") {
        clearInterval(timer);
        setClPct(null);
        setClTier(null);
        setClError(p.error || "download failed");
      }
    }, 500);
  }

  // Persist a cleanup-settings change and restart the daemon to apply it.
  async function commitCleanup(next: CleanupSettings) {
    setCleanup(next);
    await saveCleanupSettings(next);
    await restartDaemon();
    setCleanupSaved(true);
    setTimeout(() => setCleanupSaved(false), 2000);
  }

  // initialize hotkey controls from daemon status
  useEffect(() => {
    if (status) {
      setHotkey(status.hotkey);
      setHkMode(status.hotkey_mode);
    }
  }, [status?.hotkey, status?.hotkey_mode]);

  async function commitHotkey(key: string, mode: string) {
    setHotkey(key);
    setHkMode(mode);
    setHkDirty(true);
    await saveHotkey(key, mode);
  }

  async function doRestart() {
    setRestarting(true);
    await restartDaemon();
    setTimeout(() => setRestarting(false), 1600);
  }

  return (
    <div className="scroll-region flex-1 overflow-y-auto px-5 pb-8 pt-4">
      <Section title="Transcription">
        <p className="mb-2 text-[12px] leading-relaxed text-fg-soft">
          Runs on-device, so your audio never leaves this machine. The speech
          model is downloaded once on first run.
        </p>

        {/* Language: English model (best) by default; multilingual for the rest. */}
        <div className="mb-3">
          <div className="mb-1 flex items-center gap-1.5 text-[12px] font-medium text-fg">
            Language
          </div>
          <div className="flex gap-1 rounded-md border border-line bg-surface/60 p-1">
            {([
              ["english", "English"],
              ["multilingual", "Multilingual"],
            ] as [ParakeetVariant, string][]).map(([val, label]) => (
              <button
                key={val}
                onClick={() => changeVariant(val)}
                className={`flex-1 rounded px-2 py-1 text-[11px] font-medium transition-colors ${
                  variant === val ? "bg-accent text-white" : "text-fg-soft hover:text-fg"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <p className="mt-1 text-[11px] text-fg-soft">
            {variant === "multilingual"
              ? "Parakeet v3: 25 languages with automatic language detection. Switch here if you dictate in something other than English."
              : "Parakeet v2: the most accurate English model. Recommended for English."}
          </p>
        </div>

        {/* Speech model download card for the selected language variant. */}
        <div className="mb-1 rounded-md border border-line bg-surface/60 p-2.5">
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="flex items-center gap-1.5 text-[12px] font-medium text-fg">
                {variant === "multilingual" ? "Multilingual speech model" : "English speech model"}
                <span className="font-mono text-[10px] text-fg-faint">~650 MB</span>
              </div>
              <p className="text-[11px] text-fg-soft">
                {variant === "multilingual"
                  ? "NVIDIA Parakeet 0.6B v3, 25 languages, runs on the CPU, no GPU needed."
                  : "NVIDIA Parakeet 0.6B v2, best-in-class English, runs on the CPU, no GPU needed."}
              </p>
            </div>
            {speechReady ? (
              <span className="shrink-0 font-mono text-[10px] text-accent">✓ installed</span>
            ) : sPct !== null ? (
              <span className="shrink-0 font-mono text-[11px] tabular-nums text-fg-soft">{sPct}%</span>
            ) : (
              <button
                onClick={downloadSpeech}
                className="shrink-0 rounded-md bg-accent px-3 py-1.5 text-[11px] font-medium text-white transition-opacity hover:opacity-90"
              >
                Download
              </button>
            )}
          </div>
          {sPct !== null && (
            <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-line">
              <div
                className="h-full rounded-full bg-accent transition-[width] duration-300 ease-out"
                style={{ width: `${sPct}%` }}
              />
            </div>
          )}
          {sError && <p className="mt-1.5 font-mono text-[10px] text-red-500">error: {sError}</p>}
        </div>
      </Section>

      <Section title="Cleanup">
        <p className="mb-2 text-[12px] leading-relaxed text-fg-soft">
          The polish pass that strips filler and fixes punctuation. Runs fully
          on-device — your words never leave this machine, with zero per-use cost.
        </p>
        {(
          <>
            {/* Download a Quill cleanup model (public, Apache-2.0, quobi/quill).
                Only models you DON'T have yet are shown here — once downloaded a
                model drops off this list and lives in the picker below; delete it
                and it reappears here. No clutter for what's already installed. */}
            {(() => {
              const toGet = QUILL_TIERS.filter((t) => !downloaded.has(t.tier));
              if (toGet.length === 0) return null;
              return (
            <div className="mt-2 space-y-2">
              <p className="text-[11px] text-fg-faint">
                Add a model — downloaded once, then it moves to the picker below.
              </p>
              {toGet.map((t) => {
                const isActive = clTier === t.tier && clPct !== null;
                return (
                  <div key={t.tier} className="rounded-md border border-line bg-surface/60 p-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5 text-[12px] font-medium text-fg">
                          {t.label}
                          <span className="font-mono text-[10px] text-fg-faint">{t.size}</span>
                        </div>
                        <p className="truncate text-[11px] text-fg-soft">{t.blurb}</p>
                      </div>
                      {isActive ? (
                        <span className="shrink-0 font-mono text-[11px] tabular-nums text-fg-soft">{clPct}%</span>
                      ) : (
                        <button
                          onClick={() => downloadTier(t.tier)}
                          disabled={clTier !== null}
                          className="shrink-0 rounded-md bg-accent px-3 py-1.5 text-[11px] font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-40"
                        >
                          Download
                        </button>
                      )}
                    </div>
                    {isActive && (
                      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-line">
                        <div
                          className="h-full rounded-full bg-accent transition-[width] duration-300 ease-out"
                          style={{ width: `${clPct}%` }}
                        />
                      </div>
                    )}
                  </div>
                );
              })}
              {clError && <p className="font-mono text-[10px] text-red-500">error: {clError}</p>}
            </div>
              );
            })()}

            <div className="mt-3">
              <div className="flex items-center justify-between">
                <label className="text-[12px] text-fg-soft">Cleanup model</label>
                <button
                  onClick={() => { discoverLocalModels().then(setLocalModels); refreshDownloaded(); }}
                  className="font-mono text-[10px] text-fg-faint transition-colors hover:text-fg"
                  title="re-scan the models folder"
                >
                  ↻ refresh
                </button>
              </div>
              {(() => {
                // Options = every .gguf in the models dir + the current model if
                // it happens to live outside that folder (so it still shows).
                const opts = [...localModels];
                if (cleanup.local_model && !opts.includes(cleanup.local_model)) {
                  opts.unshift(cleanup.local_model);
                }
                // basename, handling BOTH separators (Windows paths use "\").
                const base = (p: string) => p.split(/[/\\]/).pop() || p;
                if (opts.length === 0) {
                  return (
                    <p className="mt-1 text-[11px] leading-relaxed text-fg-faint">
                      No <span className="font-mono">.gguf</span> found. Drop one into{" "}
                      <span className="font-mono">~/.local/share/voice-type/models/</span>{" "}
                      and hit refresh.
                    </p>
                  );
                }
                return (
                  <select
                    value={cleanup.local_model}
                    onChange={(e) => commitCleanup({ ...cleanup, local_model: e.target.value })}
                    className="mt-1 w-full rounded-md border border-line bg-surface px-2.5 py-2 text-[13px] text-fg outline-none focus:border-accent"
                  >
                    {!cleanup.local_model && <option value="">— select a model —</option>}
                    {opts.map((p) => (
                      <option key={p} value={p}>{base(p)}</option>
                    ))}
                  </select>
                );
              })()}
            </div>
            <Row label="Acceleration">
              <Toggle
                options={["auto", "gpu", "cpu"]}
                labels={{ auto: "Auto", gpu: "GPU", cpu: "CPU" }}
                value={cleanup.local_accel}
                onChange={(v) =>
                  commitCleanup({ ...cleanup, local_accel: v as "auto" | "gpu" | "cpu" })
                }
              />
            </Row>
            <p className="mt-1.5 text-[11px] leading-relaxed text-fg-faint">
              Auto uses your GPU if one's free, otherwise the CPU. GPU is much
              faster but needs a GPU-capable build.
            </p>
          </>
        )}
        {cleanupSaved && (
          <p className="mt-1.5 font-mono text-[10px] text-accent">applied ✓ daemon restarted</p>
        )}
      </Section>

      <Section title="Appearance">
        <Row label="Theme">
          <Toggle
            options={["light", "dark"]}
            value={theme}
            onChange={(v) => setTheme(v as "light" | "dark")}
          />
        </Row>
        <Row label="Text size">
          <Toggle
            options={["s", "m", "l"]}
            labels={{ s: "S", m: "M", l: "L" }}
            value={size}
            onChange={setSize}
          />
        </Row>
      </Section>

      <Section title="Hotkey">
        <Row label="Key">
          <KeyCapture value={hotkey} onCapture={(name) => commitHotkey(name, hkMode)} />
        </Row>
        <Row label="Mode">
          <Toggle
            options={["hold", "toggle"]}
            value={hkMode}
            onChange={(v) => commitHotkey(hotkey, v)}
          />
        </Row>
        <p className="pt-1 text-[11px] leading-relaxed text-fg-soft">
          {hkMode === "hold"
            ? "Hold the key while speaking; release to send."
            : "Tap once to start, tap again to stop."}
          {hkDirty && <span className="text-accent"> · restart daemon to apply</span>}
        </p>
      </Section>

      <Section title="Daemon">
        <Row label="Status">
          <span className="font-mono text-[12px] text-fg-soft">
            {status?.daemon_running ? "running" : "stopped"}
          </span>
        </Row>
        <Row label="Hotkey">
          <span className="font-mono text-[12px] text-fg-soft">
            {status ? `${status.hotkey} · ${status.hotkey_mode}` : "..."}
          </span>
        </Row>
        <Row label="Model">
          <span className="truncate font-mono text-[12px] text-fg-soft">
            {status?.cleanup_enabled ? status.model : "raw"}
          </span>
        </Row>
        <Row label="Start on login">
          <button
            role="switch"
            aria-checked={autostart}
            onClick={async () => {
              const next = !autostart;
              setAutostartState(next);
              await setAutostart(next);
            }}
            className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${
              autostart ? "bg-accent" : "bg-line"
            }`}
          >
            <span
              className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${
                autostart ? "left-[18px]" : "left-0.5"
              }`}
            />
          </button>
        </Row>
        <p className="mb-1 mt-0.5 text-[11px] text-fg-soft">
          Runs the dictation engine automatically when you log in, so it survives a reboot.
        </p>
        <button
          onClick={doRestart}
          className="mt-2 w-full rounded-md border border-line bg-surface py-2 text-[12px] font-medium text-fg transition-colors hover:border-accent"
        >
          {restarting ? "restarting…" : "Restart daemon to apply changes"}
        </button>
      </Section>

      {/* Quiet, findable bug report — opens a pre-filled GitHub issue. Out of
          the dictation flow so it never interrupts; here when you go looking. */}
      {isReportConfigured() && (
        <button
          onClick={() => openIssue()}
          className="mt-6 mb-2 block w-full text-center font-mono text-[11px] text-fg-faint transition-colors hover:text-accent"
        >
          Report a bug ↗
        </button>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-6">
      <h3 className="mb-2 font-mono text-[10px] uppercase tracking-[0.16em] text-fg-faint">
        {title}
      </h3>
      {children}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b border-line py-2.5 last:border-0">
      <span className="text-[13px] text-fg">{label}</span>
      {children}
    </div>
  );
}

function Toggle({
  options,
  value,
  onChange,
  labels,
}: {
  options: string[];
  value: string;
  onChange: (v: string) => void;
  labels?: Record<string, string>;
}) {
  return (
    <div className="flex gap-1 rounded-md bg-bg-soft p-0.5">
      {options.map((o) => (
        <button
          key={o}
          onClick={() => onChange(o)}
          className={[
            "rounded px-2.5 py-1 text-[11px] capitalize transition-colors",
            value === o ? "bg-surface text-fg shadow-sm" : "text-fg-soft",
          ].join(" ")}
        >
          {labels?.[o] ?? o}
        </button>
      ))}
    </div>
  );
}
