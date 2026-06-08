import { useEffect, useState } from "react";
import { getPersonalize, savePersonalize, restartDaemon, getCleanupSettings } from "../lib/api";

// Each Quill model is only trained for certain styles, so styles are gated by
// the loaded model's size — NOT a paid/free tier:
//   0.8B -> verbatim;  2B -> verbatim, tidy;  4B -> verbatim, tidy, formatted.
type Tier = "0.8b" | "2b" | "4b";
const TIER_RANK: Record<Tier, number> = { "0.8b": 0, "2b": 1, "4b": 2 };
const TIER_LABEL: Record<Tier, string> = { "0.8b": "0.8B", "2b": "2B", "4b": "4B" };

function modelTier(path: string): Tier {
  const p = (path || "").toLowerCase();
  if (p.includes("0.8b")) return "0.8b";
  if (p.includes("2b")) return "2b";
  if (p.includes("4b")) return "4b";
  return "4b"; // unknown / custom model -> don't restrict
}

const STYLES: { id: string; label: string; desc: string; minTier: Tier }[] = [
  { id: "verbatim", label: "Verbatim", desc: "Your exact words. Filler removed, punctuation fixed, nothing rephrased.", minTier: "0.8b" },
  { id: "tidy", label: "Tidy", desc: "Light grammar fixes and merged fragments. Keeps your meaning and voice.", minTier: "2b" },
  { id: "formatted", label: "Formatted", desc: "Tidy, plus bullet lists and paragraphs when you speak in lists.", minTier: "4b" },
];

interface Rule { target: string; variants: string }

function parseCorrections(text: string): Rule[] {
  const out: Rule[] = [];
  for (const line of text.split("\n")) {
    const i = line.indexOf(":");
    if (i < 0) continue;
    const target = line.slice(0, i).trim();
    const variants = line.slice(i + 1).trim();
    if (target) out.push({ target, variants });
  }
  return out;
}
function serializeCorrections(rules: Rule[]): string {
  return rules
    .filter((r) => r.target.trim() && r.variants.trim())
    .map((r) => `${r.target.trim()}: ${r.variants.trim()}`)
    .join("\n");
}

export function PersonalizeView() {
  const [style, setStyle] = useState("tidy");
  const [tier, setTier] = useState<Tier>("4b");
  const [rules, setRules] = useState<Rule[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "done">("idle");

  useEffect(() => {
    getPersonalize().then((p) => {
      setStyle(p.style || "tidy");
      setRules(parseCorrections(p.corrections));
      setLoaded(true);
    });
    // Which styles are offered depends on the loaded cleanup model's size.
    getCleanupSettings().then((c) => setTier(modelTier(c.local_model)));
  }, []);
  const rank = TIER_RANK[tier];

  // If the selected style isn't available for the loaded model (e.g. you were on
  // Tidy and switched to the 0.8B, which only does Verbatim), fall back to the
  // best available style so the picker never shows "nothing selected". Display
  // only — we don't overwrite the saved preference, and the daemon clamps to the
  // same style anyway, so switching back to a bigger model restores your choice.
  useEffect(() => {
    if (!loaded) return;
    const cur = STYLES.find((s) => s.id === style);
    if (cur && TIER_RANK[cur.minTier] > rank) {
      const best = [...STYLES].reverse().find((s) => TIER_RANK[s.minTier] <= rank);
      if (best && best.id !== style) setStyle(best.id);
    }
  }, [loaded, tier]); // eslint-disable-line react-hooks/exhaustive-deps

  const touch = () => setDirty(true);

  function setRule(i: number, patch: Partial<Rule>) {
    setRules((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
    touch();
  }
  function addRule() { setRules((rs) => [...rs, { target: "", variants: "" }]); touch(); }
  function removeRule(i: number) { setRules((rs) => rs.filter((_, j) => j !== i)); touch(); }

  async function save() {
    setSaveState("saving");
    await savePersonalize(style, serializeCorrections(rules));
    await restartDaemon(); // auto-apply
    setDirty(false);
    setSaveState("done");
    setTimeout(() => setSaveState("idle"), 1800);
  }

  return (
    <div className="scroll-region flex-1 overflow-y-auto px-5 pb-8 pt-4">
      {/* Cleanup style — the everyday setting */}
      <div className="mb-7">
        <label className="mb-2 block font-mono text-[10px] uppercase tracking-[0.16em] text-fg-faint">
          Cleanup style
        </label>
        <div className="space-y-2">
          {STYLES.map((s) => {
            const locked = TIER_RANK[s.minTier] > rank;
            const selected = style === s.id;
            return (
              <button
                key={s.id}
                disabled={locked}
                onClick={() => { if (!locked) { setStyle(s.id); touch(); } }}
                className={[
                  "w-full rounded-lg border px-3.5 py-3 text-left transition-colors",
                  locked
                    ? "cursor-not-allowed border-line bg-surface opacity-55"
                    : selected
                      ? "border-accent bg-accent/5"
                      : "border-line bg-surface hover:border-fg-faint",
                ].join(" ")}
              >
                <div className="flex items-center gap-2">
                  <span
                    className={`inline-block h-3 w-3 rounded-full border-2 ${selected && !locked ? "border-accent bg-accent" : "border-fg-faint"}`}
                  />
                  <span className="text-[14px] font-semibold text-fg">{s.label}</span>
                  {s.minTier !== "0.8b" && (
                    <span className="rounded-full bg-fg-faint/15 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-fg-faint">
                      {TIER_LABEL[s.minTier]}+
                    </span>
                  )}
                </div>
                <p className="mt-1 pl-5 text-[12px] leading-relaxed text-fg-soft">
                  {s.desc}
                  {locked && <span className="mt-0.5 block text-fg-faint">Needs the {TIER_LABEL[s.minTier]} model — switch in Settings → Cleanup.</span>}
                </p>
              </button>
            );
          })}
        </div>
      </div>

      {/* Corrections — the reliable name/term fixer */}
      <div className="mb-7">
        <label className="mb-1.5 block font-mono text-[10px] uppercase tracking-[0.16em] text-fg-faint">
          Corrections
        </label>
        <p className="mb-2.5 text-[12px] leading-relaxed text-fg-soft">
          Always write a name or term a certain way, no matter how it's heard.
          Add every spelling it gets mistaken for.
        </p>
        <div className="space-y-2">
          {rules.map((r, i) => (
            <div key={i} className="flex items-center gap-2">
              <input
                value={r.target}
                onChange={(e) => setRule(i, { target: e.target.value })}
                placeholder="Rabih"
                spellCheck={false}
                className="w-[34%] rounded-md border border-line bg-surface px-2.5 py-2 text-[13px] text-fg outline-none placeholder:text-fg-faint focus:border-accent"
              />
              <span className="font-mono text-[11px] text-fg-faint">←</span>
              <input
                value={r.variants}
                onChange={(e) => setRule(i, { variants: e.target.value })}
                placeholder="Robbie, Rabia, rob ee, rab ee ah"
                spellCheck={false}
                className="min-w-0 flex-1 rounded-md border border-line bg-surface px-2.5 py-2 text-[13px] text-fg outline-none placeholder:text-fg-faint focus:border-accent"
              />
              <button
                onClick={() => removeRule(i)}
                className="shrink-0 px-1 font-mono text-[14px] text-fg-faint hover:text-accent"
                title="remove"
              >
                ×
              </button>
            </div>
          ))}
        </div>
        <button
          onClick={addRule}
          className="mt-2 font-mono text-[11px] text-fg-soft transition-colors hover:text-accent"
        >
          + add a correction
        </button>
      </div>

      <button
        onClick={save}
        disabled={!loaded || (!dirty && saveState === "idle")}
        className="w-full rounded-lg bg-accent py-2.5 text-[13px] font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-40"
      >
        {saveState === "saving" ? "saving & applying…" : saveState === "done" ? "saved ✓ applied" : "Save & apply"}
      </button>
    </div>
  );
}
