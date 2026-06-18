// Typed wrappers over the Rust commands. The web layer only ever calls these
// — it has no filesystem or network access of its own.
//
// When NOT running inside Tauri (e.g. opened in a plain browser for design
// preview), these return mock data so the UI can be developed/screenshotted
// without the full Rust + daemon stack.
import { invoke } from "@tauri-apps/api/core";
import { writeText } from "@tauri-apps/plugin-clipboard-manager";

export interface Status {
  daemon_running: boolean;
  hotkey: string;
  hotkey_mode: string;
  model: string;
  tier: string;
  cleanup_enabled: boolean;
  output_mode: string;
  session: string;
}

export interface Entry {
  id: string;
  ts: string;
  kind: string;
  status: string;
  duration: number;
  raw: string;
  cleaned: string;
  audio: string;
  error: string;
}

const inTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export const getStatus = (): Promise<Status> =>
  inTauri ? invoke<Status>("get_status") : Promise.resolve(MOCK_STATUS);

export const getHistory = (): Promise<Entry[]> =>
  inTauri ? invoke<Entry[]>("get_history") : Promise.resolve(MOCK_HISTORY);

export const startDaemon = (): Promise<void> =>
  inTauri ? invoke<void>("start_daemon") : Promise.resolve();

export const retry = (id: string, audio: string): Promise<Entry> =>
  inTauri
    ? invoke<Entry>("retry", { id, audio })
    : Promise.resolve(MOCK_HISTORY.find((e) => e.id === id)!);

export const copyText = (text: string): Promise<void> =>
  inTauri ? writeText(text) : Promise.resolve(void navigator.clipboard?.writeText(text));

let mockKey = "gsk_…9f2a"; // preview only
export const apiKeyStatus = (): Promise<string> =>
  inTauri ? invoke<string>("api_key_status") : Promise.resolve(mockKey);
export const saveApiKey = (key: string): Promise<void> =>
  inTauri
    ? invoke<void>("save_api_key", { key })
    : Promise.resolve((mockKey = `${key.slice(0, 4)}…${key.slice(-4)}`, void 0));
export const saveHotkey = (key: string, mode: string): Promise<void> =>
  inTauri ? invoke<void>("save_hotkey", { key, mode }) : Promise.resolve();

let mockModel = "base";
export const getTranscribeModel = (): Promise<string> =>
  inTauri ? invoke<string>("get_transcribe_model") : Promise.resolve(mockModel);
export const saveTranscribeModel = (model: string): Promise<void> =>
  inTauri
    ? invoke<void>("save_transcribe_model", { model })
    : Promise.resolve((mockModel = model, void 0));

// Model download (first-time use of a model that isn't cached yet).
export interface DownloadProgress {
  state: "idle" | "downloading" | "done" | "error";
  model: string;
  pct: number;
  error: string;
}
// Browser-preview: pretend only "base" is cached and fake a download ramp.
const mockCached = new Set(["base"]);
let mockDl: DownloadProgress = { state: "idle", model: "", pct: 0, error: "" };
export const isModelDownloaded = (name: string): Promise<boolean> =>
  inTauri ? invoke<boolean>("is_model_downloaded", { name }) : Promise.resolve(mockCached.has(name));
export const startModelDownload = (name: string): Promise<void> => {
  if (inTauri) return invoke<void>("start_model_download", { name });
  mockDl = { state: "downloading", model: name, pct: 0, error: "" };
  const t = setInterval(() => {
    mockDl.pct += 9;
    if (mockDl.pct >= 100) { mockDl = { state: "done", model: name, pct: 100, error: "" }; mockCached.add(name); clearInterval(t); }
  }, 220);
  return Promise.resolve();
};
export const downloadProgress = (): Promise<DownloadProgress> =>
  inTauri ? invoke<DownloadProgress>("download_progress") : Promise.resolve(mockDl);

export interface Personalize {
  style: string;
  corrections: string;
}
let mockPersonalize: Personalize = { style: "verbatim", corrections: "" };
export const getPersonalize = (): Promise<Personalize> =>
  inTauri ? invoke<Personalize>("get_personalize") : Promise.resolve(mockPersonalize);
export const savePersonalize = (style: string, corrections: string): Promise<void> =>
  inTauri
    ? invoke<void>("save_personalize", { style, corrections })
    : Promise.resolve((mockPersonalize = { style, corrections }, void 0));
// Cleanup engine (cloud vs local on-device) + GPU/CPU acceleration.
export interface CleanupSettings {
  engine: "cloud" | "local";
  local_model: string;
  local_accel: "auto" | "gpu" | "cpu";
}
let mockCleanup: CleanupSettings = { engine: "cloud", local_model: "", local_accel: "auto" };
export const getCleanupSettings = (): Promise<CleanupSettings> =>
  inTauri ? invoke<CleanupSettings>("get_cleanup_settings") : Promise.resolve(mockCleanup);
export const saveCleanupSettings = (s: CleanupSettings): Promise<void> =>
  inTauri
    ? invoke<void>("save_cleanup_settings", {
        engine: s.engine, localModel: s.local_model, localAccel: s.local_accel,
      })
    : Promise.resolve((mockCleanup = s, void 0));

// Quill cleanup-model tiers, hosted public on quobi/quill (Apache 2.0).
export interface QuillTier {
  tier: "0.8b" | "2b" | "4b";
  label: string;
  size: string;
  blurb: string;
}
export const QUILL_TIERS: QuillTier[] = [
  { tier: "0.8b", label: "Quill 0.8B", size: "505 MB", blurb: "Fast, runs on any CPU. Recommended." },
  { tier: "2b", label: "Quill 2B", size: "1.2 GB", blurb: "Sharper cleanup; modest GPU helps." },
  { tier: "4b", label: "Quill 4B", size: "2.6 GB", blurb: "Best quality; needs a GPU." },
];
export const isCleanupDownloaded = (tier: string): Promise<boolean> =>
  inTauri ? invoke<boolean>("is_cleanup_downloaded", { tier }) : Promise.resolve(mockCached.has(tier));
export const startCleanupDownload = (tier: string): Promise<void> => {
  if (inTauri) return invoke<void>("start_cleanup_download", { tier });
  mockDl = { state: "downloading", model: tier, pct: 0, error: "" };
  const t = setInterval(() => {
    mockDl.pct += 7;
    if (mockDl.pct >= 100) { mockDl = { state: "done", model: tier, pct: 100, error: "" }; mockCached.add(tier); clearInterval(t); }
  }, 200);
  return Promise.resolve();
};

// whisper.cpp Vulkan STT model (ggml large-v3-turbo, ~1.6 GB). Any GPU, no CUDA.
export const isWhisperDownloaded = (): Promise<boolean> =>
  inTauri ? invoke<boolean>("is_whisper_downloaded") : Promise.resolve(mockCached.has("whisper"));
export const startWhisperDownload = (): Promise<void> => {
  if (inTauri) return invoke<void>("start_whisper_download");
  mockDl = { state: "downloading", model: "whisper", pct: 0, error: "" };
  const t = setInterval(() => {
    mockDl.pct += 6;
    if (mockDl.pct >= 100) { mockDl = { state: "done", model: "whisper", pct: 100, error: "" }; mockCached.add("whisper"); clearInterval(t); }
  }, 200);
  return Promise.resolve();
};

// NVIDIA Parakeet STT (sherpa-onnx ONNX bundle). The default local STT model —
// runs in-process on CPU, no sidecar, same on Linux + Windows.
export const isParakeetDownloaded = (): Promise<boolean> =>
  inTauri ? invoke<boolean>("is_parakeet_downloaded") : Promise.resolve(mockCached.has("parakeet"));
export const startParakeetDownload = (): Promise<void> => {
  if (inTauri) return invoke<void>("start_parakeet_download");
  mockDl = { state: "downloading", model: "parakeet-rnnt-1.1b", pct: 0, error: "" };
  const t = setInterval(() => {
    mockDl.pct += 6;
    if (mockDl.pct >= 100) { mockDl = { state: "done", model: "parakeet-rnnt-1.1b", pct: 100, error: "" }; mockCached.add("parakeet"); clearInterval(t); }
  }, 200);
  return Promise.resolve();
};

// STT engine gating. "auto" resolves per GPU: AMD -> whisper.cpp Vulkan,
// NVIDIA / no GPU -> Parakeet (CPU). recommendedStt() is what auto picks here.
export type SttEngine = "auto" | "parakeet" | "whisper";
export const recommendedStt = (): Promise<"parakeet" | "whisper"> =>
  inTauri ? invoke<"parakeet" | "whisper">("recommended_stt") : Promise.resolve("parakeet");
export const getSttEngine = (): Promise<SttEngine> =>
  inTauri ? invoke<SttEngine>("get_stt_engine") : Promise.resolve("auto");
export const setSttEngine = (engine: SttEngine): Promise<void> =>
  inTauri ? invoke<void>("set_stt_engine", { engine }) : Promise.resolve();

export const restartDaemon = (): Promise<void> =>
  inTauri ? invoke<void>("restart_daemon") : Promise.resolve();

// Every .gguf under the models dir (recursive). Drop a model in -> it appears.
export const discoverLocalModels = (): Promise<string[]> =>
  inTauri ? invoke<string[]>("discover_local_models") : Promise.resolve([]);

// Emergency unstick: release every key in the compositor if the evdev grab
// ever leaves one stuck.
export const resetKeyboard = (): Promise<void> =>
  inTauri ? invoke<void>("reset_keyboard") : Promise.resolve();

// ---- preview mocks ---------------------------------------------------------

const MOCK_STATUS: Status = {
  daemon_running: true,
  hotkey: "grave",
  hotkey_mode: "hold",
  model: "llama-3.3-70b-versatile",
  tier: "paid",
  cleanup_enabled: true,
  output_mode: "paste",
  session: "wayland",
};

function ago(mins: number): string {
  const d = new Date(Date.now() - mins * 60_000);
  // produce an ISO-ish string with offset, like the daemon writes
  return d.toString();
}

const MOCK_HISTORY: Entry[] = [
  {
    id: "1", ts: ago(3), kind: "dictation", status: "ok", duration: 4.2,
    raw: "um so basically I think we should ship the the beta on friday you know",
    cleaned: "So basically, I think we should ship the beta on Friday.",
    audio: "/x.wav", error: "",
  },
  {
    id: "2", ts: ago(28), kind: "dictation", status: "ok", duration: 2.1,
    raw: "send him the file at john dot doe at gmail dot com",
    cleaned: "Send him the file at john.doe@gmail.com.",
    audio: "/x.wav", error: "",
  },
  {
    id: "3", ts: ago(55), kind: "dictation", status: "failed", duration: 3.6,
    raw: "", cleaned: "", audio: "/x.wav", error: "groq 401: invalid api key",
  },
  {
    id: "4", ts: ago(60 * 26), kind: "dictation", status: "ok", duration: 6.8,
    raw: "let's meet tuesday no wednesday at five to go over the the numbers",
    cleaned: "Let's meet Wednesday at five to go over the numbers.",
    audio: "/x.wav", error: "",
  },
  {
    id: "5", ts: ago(60 * 50), kind: "dictation", status: "ok", duration: 1.4,
    raw: "this is honestly really clean now", cleaned: "This is honestly really clean now.",
    audio: "", error: "",
  },
  {
    id: "6", ts: ago(60 * 72), kind: "dictation", status: "ok", duration: 9.1,
    raw: "the path is home slash user slash projects new line thanks",
    cleaned: "The path is home/user/projects\nThanks.",
    audio: "/x.wav", error: "",
  },
];
