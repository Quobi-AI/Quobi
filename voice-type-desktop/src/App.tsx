import { useCallback, useEffect, useState } from "react";
import { type Entry, type Status, getHistory, getStatus } from "./lib/api";
import { TopBar, type Tab } from "./components/TopBar";
import { SetupBanner } from "./components/SetupBanner";
import { HistoryView } from "./components/HistoryView";
import { SettingsView } from "./components/SettingsView";
import { PersonalizeView } from "./components/PersonalizeView";

type Theme = "light" | "dark";

const SIZE_SCALE: Record<string, number> = { s: 0.92, m: 1, l: 1.12 };

export default function App() {
  const [tab, setTab] = useState<Tab>("history");
  const [status, setStatus] = useState<Status | null>(null);
  const [history, setHistory] = useState<Entry[]>([]);
  const [loading, setLoading] = useState(true);

  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem("vt-theme") as Theme) || "light",
  );
  const [size, setSize] = useState<string>(() => localStorage.getItem("vt-size") || "m");

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("vt-theme", theme);
  }, [theme]);

  useEffect(() => {
    (document.documentElement.style as any).zoom = String(SIZE_SCALE[size] ?? 1);
    localStorage.setItem("vt-size", size);
  }, [size]);

  const refresh = useCallback(async () => {
    try {
      const [s, h] = await Promise.all([getStatus(), getHistory()]);
      setStatus(s);
      setHistory(h);
    } catch (e) {
      console.error("refresh failed", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 4000);
    return () => clearInterval(t);
  }, [refresh]);

  const onEntryUpdated = useCallback((updated: Entry) => {
    setHistory((prev) => prev.map((e) => (e.id === updated.id ? updated : e)));
  }, []);

  const dictations = history.filter((e) => e.kind !== "scratch" || e.status === "failed");

  return (
    <div className="flex h-screen flex-col bg-bg">
      <TopBar tab={tab} setTab={setTab} status={status} theme={theme} />
      <SetupBanner />

      {tab === "history" && (
        <HistoryView entries={dictations} loading={loading} onUpdated={onEntryUpdated} />
      )}
      {tab === "personalize" && <PersonalizeView />}
      {tab === "settings" && (
        <SettingsView
          status={status}
          theme={theme}
          setTheme={setTheme}
          size={size}
          setSize={setSize}
        />
      )}
    </div>
  );
}
