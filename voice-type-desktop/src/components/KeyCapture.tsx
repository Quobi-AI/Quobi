import { useEffect, useState } from "react";
import { codeToDaemonKey, keyLabel, isRiskyHotkey } from "../lib/keymap";

export function KeyCapture({
  value,
  onCapture,
}: {
  value: string;
  onCapture: (name: string) => void;
}) {
  const [listening, setListening] = useState(false);
  const [warn, setWarn] = useState("");

  useEffect(() => {
    if (!listening) return;
    setWarn("");

    function onKey(e: KeyboardEvent) {
      e.preventDefault();
      e.stopPropagation();
      if (e.code === "Escape") {
        setListening(false);
        return;
      }
      const name = codeToDaemonKey(e.code);
      if (!name) {
        setWarn(`"${e.key}" can't be used. Try a function key, backtick, or a modifier.`);
        return;
      }
      onCapture(name);
      setWarn(isRiskyHotkey(name) ? "Heads up: that key is also used for normal typing." : "");
      setListening(false);
    }

    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [listening, onCapture]);

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        onClick={() => setListening((v) => !v)}
        className={[
          "min-w-[140px] rounded-md border px-3 py-1.5 text-center font-mono text-[12px] transition-colors",
          listening
            ? "border-accent bg-accent/10 text-accent"
            : "border-line bg-surface text-fg hover:border-accent",
        ].join(" ")}
      >
        {listening ? "press any key…" : keyLabel(value)}
      </button>
      {warn && <span className="max-w-[200px] text-right text-[10px] leading-tight text-accent">{warn}</span>}
    </div>
  );
}
