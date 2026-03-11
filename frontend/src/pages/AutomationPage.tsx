import { useEffect, useRef, useState } from "react";
import { formatLiveCountdown } from "@/lib/utils";
import { Clock3, Download, Info, Loader2, Play, Square, Upload, RotateCcw, Zap, Search, AlertCircle } from "lucide-react";
import { useAutomationSettings, useN8nTiming, useOpenAIModels, useQueue, useQueuePreview } from "@/hooks/useEngageFlow";
import { api } from "@/lib/api";
import type { AutomationSettings } from "@/lib/types";
import { toast } from "sonner";
import { useBackend } from "@/context/BackendContext";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

const allDays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
function toAmPmLabel(value: string) {
  const match = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(value);
  if (!match) {
    return value;
  }
  const hours24 = Number(match[1]);
  const minutes = match[2];
  const isPm = hours24 >= 12;
  const hours12 = hours24 % 12 || 12;
  return `${String(hours12).padStart(2, "0")}:${minutes} ${isPm ? "PM" : "AM"}`;
}

function splitTime(value: string) {
  const match = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(value);
  const fallback = { hour12: 9, minute: 0, meridiem: "AM" as "AM" | "PM" };
  if (!match) return fallback;
  const h24 = Number(match[1]);
  const minute = Number(match[2]);
  const meridiem: "AM" | "PM" = h24 >= 12 ? "PM" : "AM";
  const hour12 = h24 % 12 || 12;
  return { hour12, minute, meridiem };
}

function buildTime(hour12: number, minute: number, meridiem: "AM" | "PM") {
  const h = Math.min(12, Math.max(1, hour12));
  const m = Math.min(59, Math.max(0, minute));
  const h24 = meridiem === "AM" ? (h === 12 ? 0 : h) : (h === 12 ? 12 : h + 12);
  return `${String(h24).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

const FALLBACK_MODELS = ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo", "o3-mini", "o1-mini"];

// Filter to text chat completion models — exclude non-chat variants and dated snapshots
const CHAT_EXCLUDE_PATTERNS = ["-audio", "-realtime", "-tts", "-transcribe", "-search", "-diarize", "-instruct", "-image", "chatgpt-image", "gpt-image", "gpt-audio", "gpt-realtime", "-codex"];
const DATE_SNAPSHOT_RE = /-(20\d{2})-/;
function isChatModel(id: string): boolean {
  if (!id.startsWith("gpt-") && !id.startsWith("o1") && !id.startsWith("o3") && !id.startsWith("o4")) return false;
  if (CHAT_EXCLUDE_PATTERNS.some(p => id.includes(p))) return false;
  // Exclude dated snapshots (e.g. gpt-4o-2024-08-06) — keep canonical aliases only
  if (DATE_SNAPSHOT_RE.test(id)) return false;
  return true;
}

function ModelPicker({ label, value, defaultVal, onChange, allowEmpty, emptyLabel, liveModels, liveLoading, liveError }: {
  label: string;
  value: string;
  defaultVal: string;
  onChange: (v: string) => void;
  allowEmpty?: boolean;
  emptyLabel?: string;
  liveModels: string[];
  liveLoading: boolean;
  liveError?: string;
}) {
  const [customMode, setCustomMode] = useState(false);
  const [customText, setCustomText] = useState("");

  const models = liveModels.length > 0 ? liveModels : FALLBACK_MODELS;
  const isInList = models.includes(value) || (allowEmpty && value === "");
  const showCustom = customMode || (!isInList && value !== "");

  return (
    <div>
      <label className="block text-xs font-medium text-muted-foreground mb-1">{label}</label>
      {!showCustom ? (
        <select
          className="w-full rounded-md border bg-background px-3 py-2 text-sm"
          value={isInList ? value : "__custom__"}
          onChange={(e) => {
            if (e.target.value === "__custom__") {
              setCustomMode(true);
              setCustomText(value || "");
              return;
            }
            onChange(e.target.value);
          }}
        >
          {allowEmpty && <option value="">{emptyLabel}</option>}
          {models.map(m => (
            <option key={m} value={m}>{m}{m === defaultVal && !allowEmpty ? " (default)" : ""}</option>
          ))}
          <option value="__custom__">Custom model name...</option>
        </select>
      ) : (
        <div className="flex gap-2 items-center">
          <input
            type="text"
            className="flex-1 rounded-md border bg-background px-3 py-2 text-sm font-mono"
            value={customMode ? customText : value}
            onChange={(e) => {
              setCustomText(e.target.value);
              if (e.target.value.trim()) onChange(e.target.value.trim());
            }}
            onBlur={() => { if (customText.trim()) onChange(customText.trim()); }}
            placeholder="e.g. ft:gpt-4o-mini:my-org:name:id"
            autoFocus={customMode}
          />
          <button
            className="px-2.5 py-2 rounded-md text-xs text-muted-foreground hover:bg-muted border border-border whitespace-nowrap"
            onClick={() => { setCustomMode(false); setCustomText(""); onChange(defaultVal); }}
          >List</button>
        </div>
      )}
      {showCustom && value && !isInList && (
        <p className="text-[10px] text-muted-foreground/60 mt-1">Custom: <span className="font-mono">{value}</span></p>
      )}
      {liveError && liveModels.length === 0 && (
        <p className="text-[10px] text-orange-500/80 mt-1">Live model list unavailable. Showing fallback presets.</p>
      )}
    </div>
  );
}

export default function AutomationPage() {
  const settingsQuery = useAutomationSettings();
  const openaiModelsQuery = useOpenAIModels();
  const { engineStatus, logs, refresh } = useBackend();
  const [settings, setSettings] = useState<AutomationSettings | null>(settingsQuery.data ?? null);
  const [showTooltip, setShowTooltip] = useState(false);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [actionPending, setActionPending] = useState<"start" | "resume" | "stop" | null>(null);
  const [resetTasksPending, setResetTasksPending] = useState(false);
  const [nowMs, setNowMs] = useState(Date.now());
  const saveTimerRef = useRef<number | null>(null);
  const lastSavedJsonRef = useRef<string>("");

  useEffect(() => {
    if (settingsQuery.data) {
      setSettings(settingsQuery.data);
      const json = JSON.stringify(settingsQuery.data);
      lastSavedJsonRef.current = json;
      setSaveState("idle");
    }
  }, [settingsQuery.data]);

  // Tick countdown every second for live display
  useEffect(() => {
    const t = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  const update = (patch: Partial<AutomationSettings>) => setSettings((prev) => (prev ? { ...prev, ...patch } : prev));
  const toSafeNumber = (value: string, fallback: number) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  };

  const updateAndSaveNow = (patch: Partial<AutomationSettings>) => {
    if (!settings) return;
    const next = { ...settings, ...patch };
    setSettings(next);
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
    saveSettings(next, true).catch(() => {
      toast.error("Failed to save automation settings");
    });
  };

  const saveSettings = async (payload?: AutomationSettings, silent = false) => {
    const target = payload ?? settings;
    if (!target) return;
    try {
      setSaveState("saving");
      const updated = await api.updateAutomationSettings(target);
      const json = JSON.stringify(updated);
      lastSavedJsonRef.current = json;
      setSettings(updated);
      setSaveState("saved");
      if (!silent) {
        toast.success("Automation settings updated");
      }
      window.setTimeout(() => setSaveState((prev) => (prev === "saved" ? "idle" : prev)), 1200);
    } catch (error) {
      setSaveState("error");
      if (!silent) {
        throw error;
      }
    }
  };

  useEffect(() => {
    if (!settings) return;
    const json = JSON.stringify(settings);
    if (json === lastSavedJsonRef.current) return;

    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current);
    }
    saveTimerRef.current = window.setTimeout(() => {
      saveSettings(settings, true).catch(() => {
        toast.error("Failed to save automation settings");
      });
    }, 450);

    return () => {
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current);
      }
    };
  }, [settings]);

  const toggleDay = (day: string) => {
    if (!settings) return;
    update({
      activeDays: settings.activeDays.includes(day)
        ? settings.activeDays.filter((d) => d !== day)
        : [...settings.activeDays, day],
    });
  };

  const handleEngineAction = async (action: "start" | "resume" | "stop") => {
    setActionPending(action);
    try {
      if (action === "start") await api.automationStart();
      if (action === "resume") await api.automationResume();
      if (action === "stop") await api.automationStop();
      await refresh();
      const successText: Record<typeof action, string> = {
        start: "Automation started",
        resume: "Automation resumed",
        stop: "Automation stopped",
      };
      toast.success(successText[action]);
    } catch (error) {
      const message = error instanceof Error ? error.message : `Failed to ${action} automation`;
      toast.error(message);
    } finally {
      setActionPending(null);
    }
  };

  const handleResetTasks = async () => {
    const ok = window.confirm("Delete all queued tasks?");
    if (!ok) return;
    setResetTasksPending(true);
    try {
      const result = await api.automationResetTasks();
      await refresh();
      toast.success(`Removed ${result.queueDeleted} queued tasks.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to reset tasks";
      toast.error(message);
    } finally {
      setResetTasksPending(false);
    }
  };

  const handleExportBlacklist = () => {
    if (!settings) return;
    const json = JSON.stringify(settings.blacklistTerms, null, 2);
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "blacklist.json";
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleImportBlacklist = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !settings) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      try {
        const terms = JSON.parse(ev.target?.result as string);
        if (Array.isArray(terms)) {
          update({ blacklistTerms: [...new Set([...settings.blacklistTerms, ...terms.map(String)])] });
        }
      } catch {
        return;
      }
    };
    reader.readAsText(file);
    e.target.value = "";
  };

  /* ── n8n scan-now handler (hooks must be before any early return) ── */
  const [scanPending, setScanPending] = useState(false);
  const [scanResult, setScanResult] = useState<{ scanned: number; posts_found: number; queued: number; skipped: number; errors: string[]; ts: string } | null>(null);
  const isN8nMode = settings?.orchestrationMode === "n8n";
  const n8nTimingQuery = useN8nTiming(isN8nMode);
  const n8nTiming = n8nTimingQuery.data ?? null;
  const queueQuery = useQueue();
  const queuePreviewQuery = useQueuePreview();
  const realQueue = (() => {
    const d = queueQuery.data;
    if (!d) return [];
    if (typeof d === "object" && "items" in d) return (d as any).items ?? [];
    return Array.isArray(d) ? d : [];
  })() as Array<{ id: string; profile?: string; community?: string; scheduledFor?: string; keyword?: string; [k: string]: any }>;
  const projectedQueue = (queuePreviewQuery.data ?? []).filter((p: any) => p.isProjected);

  /* ── live automation log from logs context ── */
  const automationLogs = (logs ?? [])
    .filter((l) => {
      const m = (l.message || "").toLowerCase();
      return m.includes("n8n") || m.includes("scan") || m.includes("queue") || m.includes("execute") || m.includes("executor") || m.includes("auto-scan") || m.includes("cron") || m.includes("comment");
    })
    .slice(0, 20);

  if (!settings) {
    return (
      <div className="p-4 md:p-6 lg:p-8 pt-16 md:pt-6 lg:pt-8 max-w-2xl">
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-foreground">Automation Settings</h1>
          <p className="text-sm text-muted-foreground mt-1">Loading configuration...</p>
        </div>
      </div>
    );
  }
  const isEngineStopped = !engineStatus?.isRunning;
  void 0; // connection rest UI removed — feature is opt-in backend-only

  const handleScanNow = async () => {
    setScanPending(true);
    setScanResult(null);
    try {
      const r = await api.n8nScanNow();
      setScanResult({ ...r, ts: new Date().toLocaleTimeString() });
      n8nTimingQuery.refetch();
      toast.success(`Scan complete: ${r.queued} queued, ${r.posts_found} posts found`);
    } catch (error) {
      const msg = error instanceof Error ? error.message : "Scan failed";
      toast.error(msg);
    } finally {
      setScanPending(false);
    }
  };

  const formatAgo = (raw: string | null) => {
    if (!raw) return "never";
    const parsed = new Date(raw);
    if (isNaN(parsed.getTime())) {
      return raw;
    }
    const diff = Date.now() - parsed.getTime();
    // Clock skew tolerance: if timestamp is slightly in the future (< 2 min), treat as "just now"
    // If far in the future, it's a timezone mismatch — use absolute diff
    const absDiff = Math.abs(diff);
    if (diff < 0 && absDiff > 120000) {
      // Likely timezone-naive string parsed wrong — show as relative using abs
      const s = Math.floor(absDiff / 1000);
      if (s < 60) return "just now";
      const m = Math.floor(s / 60);
      if (m < 60) return `${m}m ago`;
      const h = Math.floor(m / 60);
      if (h < 24) return `${h}h ${m % 60}m ago`;
      const d = Math.floor(h / 24);
      return `${d}d ${h % 24}h ago`;
    }
    if (diff < 0) return "just now";
    const s = Math.floor(diff / 1000);
    if (s < 60) return "just now";
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ${m % 60}m ago`;
    const d = Math.floor(h / 24);
    return `${d}d ${h % 24}h ago`;
  };

  return (
    <div className="p-4 md:p-6 lg:p-8 pt-16 md:pt-6 lg:pt-8 max-w-2xl">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-foreground">Automation Settings</h1>
        <p className="text-sm text-muted-foreground mt-1">Global configuration inherited by all profiles</p>
      </div>

      <div className="space-y-6">
        {/* ── AUTOMATION CONTROL — N8N MODE ── */}
        {isN8nMode ? (
          <>
            <SettingsCard title="n8n Automation Status">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <span className={`inline-block w-2 h-2 rounded-full ${settings.masterEnabled ? "bg-green-500" : "bg-red-500"}`} />
                  <p className="text-sm text-foreground font-medium">
                    {settings.masterEnabled ? "Armed — External automation active" : "Disarmed — Automation paused"}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={handleScanNow}
                    disabled={scanPending || !settings.masterEnabled}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-primary text-primary-foreground text-xs font-medium hover:bg-primary/90 transition-colors disabled:opacity-60"
                  >
                    {scanPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}
                    {scanPending ? "Scanning..." : "Run Scan Now"}
                  </button>
                  <button
                    onClick={handleResetTasks}
                    disabled={resetTasksPending}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border text-xs font-medium text-foreground hover:bg-muted transition-colors disabled:opacity-60"
                  >
                    {resetTasksPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RotateCcw className="w-3.5 h-3.5" />}
                    Reset Tasks
                  </button>
                </div>
              </div>

              {/* n8n timing strip */}
              {n8nTiming && (
                <div className="text-xs text-muted-foreground mt-2 pt-2 border-t border-border space-y-2">
                  {/* Past events */}
                  <div className="grid grid-cols-2 gap-x-6 gap-y-1">
                    <div>Last scan: <span className="text-foreground">{formatAgo(n8nTiming.lastScanTime)}</span></div>
                    <div>Last execute: <span className="text-foreground">{formatAgo(n8nTiming.lastExecuteTime)}</span></div>
                    <div>Last queued: <span className="text-foreground">{formatAgo(n8nTiming.lastQueueInsert)}</span></div>
                    <div>Pending: <span className="text-foreground">{n8nTiming.pendingQueueItems}</span></div>
                  </div>
                  {/* Upcoming countdowns */}
                  {settings.masterEnabled && (
                    <div className="grid grid-cols-2 gap-x-6 gap-y-1 pt-1 border-t border-border/50">
                      <div>Next scan: <span className="text-foreground font-mono">{(() => {
                        if (!n8nTiming.lastScanTime) return "unknown";
                        const lastScan = new Date(n8nTiming.lastScanTime).getTime();
                        if (isNaN(lastScan)) return "unknown";
                        const interval = (n8nTiming.executorIntervalSeconds || 120) * 1000;
                        const nextScanMs = lastScan + interval;
                        const sec = Math.floor((nextScanMs - nowMs) / 1000);
                        if (sec > 0) return formatLiveCountdown(sec);
                        // Already overdue — show cycle info
                        const elapsed = Math.floor((nowMs - lastScan) / 1000);
                        const cyclePos = elapsed % Math.max(1, n8nTiming.executorIntervalSeconds || 120);
                        const remaining = Math.max(0, (n8nTiming.executorIntervalSeconds || 120) - cyclePos);
                        return remaining > 0 ? formatLiveCountdown(remaining) : "imminent";
                      })()}</span></div>
                      <div>Next execute check: <span className="text-foreground font-mono">{(() => {
                        // Executor loop runs on a fixed interval from startup, not from last event
                        // Best estimate: use modular arithmetic on the interval
                        const interval = n8nTiming.executorIntervalSeconds || 120;
                        if (!n8nTiming.lastScanTime && !n8nTiming.lastExecuteTime) return "unknown";
                        // Use the most recent event as anchor
                        const anchors = [n8nTiming.lastScanTime, n8nTiming.lastExecuteTime].filter(Boolean).map(t => new Date(t!).getTime()).filter(t => !isNaN(t));
                        if (anchors.length === 0) return "unknown";
                        const latest = Math.max(...anchors);
                        const elapsed = Math.floor((nowMs - latest) / 1000);
                        const cyclePos = elapsed % Math.max(1, interval);
                        const remaining = Math.max(0, interval - cyclePos);
                        return remaining > 0 ? formatLiveCountdown(remaining) : "imminent";
                      })()}</span></div>
                      <div className="col-span-2">Next planned comment: <span className="text-foreground font-mono">{(() => {
                        // Real queue first
                        if (n8nTiming.nextScheduledFor) {
                          const d = new Date(n8nTiming.nextScheduledFor).getTime();
                          if (!isNaN(d)) {
                            const sec = Math.max(0, Math.floor((d - nowMs) / 1000));
                            return sec <= 0 ? "ready now" : formatLiveCountdown(sec);
                          }
                        }
                        // Projected fallback
                        if (projectedQueue.length > 0) {
                          const sorted = projectedQueue
                            .map((p: any) => new Date(String(p.scheduledFor || "")).getTime())
                            .filter((t: number) => !isNaN(t) && t > nowMs)
                            .sort((a: number, b: number) => a - b);
                          if (sorted.length > 0) {
                            const sec = Math.max(0, Math.floor((sorted[0] - nowMs) / 1000));
                            return sec > 0 ? `${formatLiveCountdown(sec)} (projected)` : "imminent";
                          }
                        }
                        return "no upcoming actions";
                      })()}</span></div>
                      {/* Next planned target */}
                      {(() => {
                        // Show target profile > community for next planned
                        let target: { profile?: string; community?: string } | null = null;
                        let isProjected = false;
                        if (realQueue.length > 0 && n8nTiming.nextScheduledFor) {
                          const sorted = [...realQueue].filter(i => i.scheduledFor).sort((a, b) =>
                            new Date(a.scheduledFor!).getTime() - new Date(b.scheduledFor!).getTime()
                          );
                          if (sorted.length > 0) target = sorted[0];
                        } else if (projectedQueue.length > 0) {
                          const sorted = [...projectedQueue].filter((p: any) => p.scheduledFor).sort((a: any, b: any) =>
                            new Date(a.scheduledFor).getTime() - new Date(b.scheduledFor).getTime()
                          );
                          if (sorted.length > 0) { target = sorted[0] as any; isProjected = true; }
                        }
                        return target ? (
                          <div className={`col-span-2 ${isProjected ? "italic text-muted-foreground/70" : ""}`}>
                            {isProjected ? "Next planned" : "Next queued"}: <span className="text-foreground">
                              {(target as any).profile} &gt; {(target as any).community}
                            </span>
                          </div>
                        ) : null;
                      })()}
                    </div>
                  )}
                  {settings.masterEnabled && n8nTiming.executorIntervalSeconds && (
                    <div className="pt-1 border-t border-border/50">
                      Executor interval: <span className="text-foreground">{Math.round(n8nTiming.executorIntervalSeconds / 60)}m</span>
                    </div>
                  )}
                </div>
              )}

              {/* scan result */}
              {scanResult && (
                <div className="mt-2 p-2 rounded-lg bg-muted/50 text-xs text-muted-foreground">
                  <span className="text-foreground font-medium">{scanResult.ts}</span> — Scanned {scanResult.scanned} communities, found {scanResult.posts_found} posts, queued {scanResult.queued}, skipped {scanResult.skipped}
                  {scanResult.errors.length > 0 && (
                    <div className="mt-1 text-destructive">
                      {scanResult.errors.map((e, i) => <div key={i}>{e}</div>)}
                    </div>
                  )}
                </div>
              )}
            </SettingsCard>

            {/* ── LIVE AUTOMATION LOG ── */}
            <SettingsCard title="Live Automation Feed">
              <div className="space-y-1 max-h-56 overflow-y-auto font-mono bg-background/50 rounded-md p-2 border border-border/50">
                {automationLogs.length > 0 ? automationLogs.map((log, i) => {
                  const ts = log.timestamp || "";
                  const isErr = (log.status || "").toLowerCase() === "error";
                  const isSuccess = (log.status || "").toLowerCase() === "success";
                  return (
                    <div key={log.id || i} className={`text-[11px] leading-relaxed ${isErr ? "text-destructive" : isSuccess ? "text-green-500" : "text-muted-foreground"} flex gap-2`}>
                      <span className="text-foreground/40 shrink-0">{ts}</span>
                      <span className="truncate">{log.message}</span>
                    </div>
                  );
                }) : (
                  <div className="text-[11px] text-muted-foreground py-4 text-center">
                    {settings.masterEnabled ? "Waiting for automation events..." : "Automation is stopped"}
                  </div>
                )}
              </div>
            </SettingsCard>
          </>
        ) : (
          /* ── AUTOMATION CONTROL — INTERNAL MODE ── */
          <SettingsCard title="Automation Control">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm text-foreground font-medium">
                  {engineStatus?.isRunning ? (engineStatus.isPaused ? "Paused" : "Running") : "Stopped"}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {!engineStatus?.isRunning && (
                  <button
                    onClick={() => handleEngineAction("start")}
                    disabled={actionPending !== null}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-primary text-primary-foreground text-xs font-medium hover:bg-primary/90 transition-colors disabled:opacity-60"
                  >
                    {actionPending === "start" ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
                    Start
                  </button>
                )}
                {engineStatus?.isRunning && engineStatus.isPaused && (
                  <button
                    onClick={() => handleEngineAction("resume")}
                    disabled={actionPending !== null}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border text-xs font-medium text-foreground hover:bg-muted transition-colors disabled:opacity-60"
                  >
                    {actionPending === "resume" ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
                    Resume
                  </button>
                )}
                {engineStatus?.isRunning && (
                  <button
                    onClick={() => handleEngineAction("stop")}
                    disabled={actionPending !== null}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-destructive text-xs font-medium text-destructive hover:bg-destructive/10 transition-colors disabled:opacity-60"
                  >
                    {actionPending === "stop" ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Square className="w-3.5 h-3.5" />}
                    Stop
                  </button>
                )}
                <TooltipProvider delayDuration={150}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className={`inline-flex ${!isEngineStopped ? "cursor-not-allowed" : ""}`}>
                        <button
                          onClick={handleResetTasks}
                          disabled={!isEngineStopped || actionPending !== null || resetTasksPending}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border text-xs font-medium text-foreground hover:bg-muted transition-colors disabled:opacity-60 disabled:pointer-events-none"
                        >
                          {resetTasksPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RotateCcw className="w-3.5 h-3.5" />}
                          Reset Tasks
                        </button>
                      </span>
                    </TooltipTrigger>
                    {!isEngineStopped && (
                      <TooltipContent side="top" className="max-w-56 text-xs">
                        Stop automation to clear queued tasks.
                      </TooltipContent>
                    )}
                  </Tooltip>
                </TooltipProvider>
              </div>
            </div>
          </SettingsCard>
        )}

        <SettingsCard>
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold text-foreground">Master Automation</h3>
              <p className="text-xs text-muted-foreground mt-0.5">{settings.masterEnabled ? "All automation is active" : "All automation is paused"}</p>
            </div>
            <Toggle checked={settings.masterEnabled} onChange={() => updateAndSaveNow({ masterEnabled: !settings.masterEnabled })} />
          </div>
        </SettingsCard>

        <SettingsCard title="Comment automation orchestration">
          <div>
            <label className="text-xs text-muted-foreground mb-1 block">Who drives comment automation</label>
            <div className="flex gap-2 mt-1">
              <button
                type="button"
                onClick={() => updateAndSaveNow({ orchestrationMode: "internal" })}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${(settings.orchestrationMode ?? "internal") === "internal" ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"}`}
              >
                Internal
              </button>
              <button
                type="button"
                onClick={() => updateAndSaveNow({ orchestrationMode: "n8n" })}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${settings.orchestrationMode === "n8n" ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"}`}
              >
                n8n
              </button>
            </div>
            <p className="text-[11px] text-muted-foreground mt-1.5">
              Internal: Skoollindy scheduler scans, queues and posts comments. n8n: comment automation is orchestrated externally by n8n; scheduler does not run comment flow.
            </p>
            {settings.orchestrationMode === "n8n" && (
              <div className="mt-2 p-2 rounded-lg bg-muted/50 text-xs text-muted-foreground">
                Current mode: n8n. Comment automation is externally orchestrated by n8n. All rules and settings above still apply; n8n reads them from the backend API.
              </div>
            )}
          </div>
        </SettingsCard>

        <SettingsCard title="Global Caps & Timing">
          <div>
            <label className="text-xs text-muted-foreground mb-1 block">Global Daily Cap Per Account</label>
            <p className="text-[11px] text-muted-foreground mb-1.5">
              Each connected account can perform up to this many comment actions per day - DM is unlimited
            </p>
            <SettingsInput type="number" value={settings.globalDailyCapPerAccount} onChange={(e) => update({ globalDailyCapPerAccount: toSafeNumber(e.target.value, settings.globalDailyCapPerAccount) })} />
          </div>
          <div>
            <label className="text-xs text-muted-foreground mb-1 block">Actions Per Account Per Pass</label>
            <p className="text-[11px] text-muted-foreground mb-1.5">
              How many comment actions each account performs before switching to the next account
            </p>
            <SettingsInput
              type="number"
              min={1}
              value={settings.queuePrefillMaxPerProfilePerPass}
              onChange={(e) =>
                update({
                  queuePrefillMaxPerProfilePerPass: Math.max(
                    1,
                    toSafeNumber(e.target.value, settings.queuePrefillMaxPerProfilePerPass),
                  ),
                })
              }
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">Global Delay Min (sec)</label>
              <SettingsInput type="number" value={settings.delayMin} onChange={(e) => update({ delayMin: toSafeNumber(e.target.value, settings.delayMin) })} />
            </div>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">Global Delay Max (sec)</label>
              <SettingsInput type="number" value={settings.delayMax} onChange={(e) => update({ delayMax: toSafeNumber(e.target.value, settings.delayMax) })} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">Global Run Window From</label>
              <TimeInput id="run-from" value={settings.runFrom} onChange={(value) => updateAndSaveNow({ runFrom: value })} />
            </div>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">Global Run Window To</label>
              <TimeInput id="run-to" value={settings.runTo} onChange={(value) => updateAndSaveNow({ runTo: value })} />
            </div>
          </div>
          <div>
            <label className="text-xs text-muted-foreground mb-2 block">Global Active Days</label>
            <div className="flex gap-1.5">
              {allDays.map((day) => (
                <button
                  key={day}
                  onClick={() => toggleDay(day)}
                  className={`px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${settings.activeDays.includes(day) ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"}`}
                >
                  {day}
                </button>
              ))}
            </div>
          </div>
        </SettingsCard>

        <SettingsCard title="Blacklist">
          <div className="flex items-center justify-between">
            <div>
              <span className="text-sm text-foreground">Enable Blacklist</span>
              <p className="text-[11px] text-muted-foreground mt-0.5">Skip posts containing blacklisted terms/phrases</p>
            </div>
            <Toggle checked={settings.blacklistEnabled} onChange={() => updateAndSaveNow({ blacklistEnabled: !settings.blacklistEnabled })} />
          </div>
          <div>
            <label className="text-xs text-muted-foreground mb-1 block">Blacklisted Terms (one per line)</label>
            <textarea
              value={settings.blacklistTerms.join("\n")}
              onChange={(e) => update({ blacklistTerms: e.target.value.split("\n").filter((t) => t.trim()) })}
              rows={5}
              disabled={!settings.blacklistEnabled}
              className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground resize-none focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50 font-mono"
              placeholder="spam&#10;scam&#10;mlm"
            />
          </div>
          <div className="flex gap-2">
            <label className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border text-xs font-medium text-foreground hover:bg-muted transition-colors cursor-pointer">
              <Upload className="w-3.5 h-3.5" /> Import JSON
              <input type="file" accept=".json" onChange={handleImportBlacklist} className="hidden" />
            </label>
            <button onClick={handleExportBlacklist} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border text-xs font-medium text-foreground hover:bg-muted transition-colors">
              <Download className="w-3.5 h-3.5" /> Export
            </button>
          </div>
          <div className="bg-muted/30 rounded-lg p-3">
            <p className="text-[11px] text-muted-foreground">
              If a blacklist term is found in a post, the action is skipped and never queued. Logged as{" "}
              <code className="bg-muted px-1 py-0.5 rounded text-[10px]">SKIPPED_BLACKLIST</code>.
            </p>
          </div>
        </SettingsCard>

        <SettingsCard title="Global Prompt Defaults">
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-xs text-muted-foreground">GLOBAL COMMENT FALLBACK ENABLED</label>
              <Toggle checked={settings.commentFallbackEnabled} onChange={() => updateAndSaveNow({ commentFallbackEnabled: !settings.commentFallbackEnabled })} size="sm" />
            </div>
            <p className="text-[11px] text-muted-foreground mb-1.5">Used when a post is found but no keyword matches. If disabled, post is skipped.</p>
            <label className="text-xs text-muted-foreground mb-1 block">GLOBAL COMMENT FALLBACK</label>
            <textarea
              value={settings.commentFallbackPrompt}
              onChange={(e) => update({ commentFallbackPrompt: e.target.value })}
              rows={3}
              disabled={!settings.commentFallbackEnabled}
              className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground resize-none focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
            />
          </div>
          <div>
            <label className="text-xs text-muted-foreground mb-1 block">GLOBAL MASTER DM FALLBACK</label>
            <p className="text-[11px] text-muted-foreground mb-1.5">Used when no keyword rule DM prompt exists</p>
            <textarea
              value={settings.dmFallbackPrompt}
              onChange={(e) => update({ dmFallbackPrompt: e.target.value })}
              rows={3}
              className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground resize-none focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
        </SettingsCard>

        <SettingsCard title="AI Model Selection">
          <p className="text-xs text-muted-foreground mb-3">Models loaded live from your OpenAI API key. Use "Custom" for fine-tuned or unlisted models.</p>
          <div className="space-y-3">
            {openaiModelsQuery.isLoading && <p className="text-[11px] text-muted-foreground animate-pulse">Loading models from OpenAI...</p>}
            <ModelPicker label="DM Reply Model" value={settings.dmModel ?? "gpt-4o-mini"} defaultVal="gpt-4o-mini" onChange={(v) => updateAndSaveNow({ dmModel: v })} liveModels={(openaiModelsQuery.data?.models ?? []).map(m => m.id).filter(isChatModel)} liveLoading={openaiModelsQuery.isLoading} liveError={openaiModelsQuery.data?.error} />
            <ModelPicker label="Follow-Up Model" value={settings.followUpModel ?? ""} defaultVal="" onChange={(v) => updateAndSaveNow({ followUpModel: v })} allowEmpty emptyLabel="Same as DM model" liveModels={(openaiModelsQuery.data?.models ?? []).map(m => m.id).filter(isChatModel)} liveLoading={openaiModelsQuery.isLoading} liveError={openaiModelsQuery.data?.error} />
            <ModelPicker label="Comment Model" value={settings.commentModel ?? "gpt-3.5-turbo"} defaultVal="gpt-3.5-turbo" onChange={(v) => updateAndSaveNow({ commentModel: v })} liveModels={(openaiModelsQuery.data?.models ?? []).map(m => m.id).filter(isChatModel)} liveLoading={openaiModelsQuery.isLoading} liveError={openaiModelsQuery.data?.error} />
          </div>
        </SettingsCard>

        <SettingsCard title="Follow-Up Automation">
          <p className="text-xs text-muted-foreground mb-3">Automatically send follow-up DMs when contacts don't reply after an AI message.</p>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm text-foreground">Enable Follow-Ups</span>
              <Toggle checked={!!settings.followUpEnabled} onChange={() => updateAndSaveNow({ followUpEnabled: !settings.followUpEnabled })} size="sm" />
            </div>
            {settings.followUpEnabled && (
              <>
                <div>
                  <label className="block text-xs font-medium text-muted-foreground mb-1">Follow-Up Delay</label>
                  <select
                    className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                    value={settings.followUpDelaySeconds ?? 86400}
                    onChange={(e) => updateAndSaveNow({ followUpDelaySeconds: Number(e.target.value) })}
                  >
                    <option value={1}>1 second (test)</option>
                    <option value={60}>1 minute (test)</option>
                    <option value={3600}>1 hour</option>
                    <option value={10800}>3 hours</option>
                    <option value={86400}>1 day</option>
                    <option value={172800}>2 days</option>
                    <option value={259200}>3 days</option>
                    <option value={2592000}>1 month</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-muted-foreground mb-1">Max Follow-Ups per Conversation</label>
                  <select
                    className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                    value={settings.followUpMaxCount ?? 2}
                    onChange={(e) => updateAndSaveNow({ followUpMaxCount: Number(e.target.value) })}
                  >
                    <option value={0}>0 (disabled)</option>
                    <option value={1}>1</option>
                    <option value={2}>2</option>
                    <option value={3}>3</option>
                    <option value={5}>5</option>
                  </select>
                </div>
                <p className="text-xs text-muted-foreground">Per-keyword overrides: dmReplyDelay (delay in seconds) and dmMaxReplies (max count) on keyword rules take priority over these global defaults.</p>
              </>
            )}
          </div>
        </SettingsCard>

        <SettingsCard title="Staged DM Prompts">
          <p className="text-xs text-muted-foreground mb-3">Optional per-stage prompt overrides. If empty, the default DM prompt is used. Per-keyword-rule staged prompts override these globals.</p>
          {(() => {
            const stages = settings.dmPromptStages ?? {};
            const updateStage = (key: string, val: string) => {
              const next = { ...stages, [key]: val };
              if (!val.trim()) delete next[key];
              updateAndSaveNow({ dmPromptStages: next });
            };
            const stageLabels: [string, string][] = [
              ["first", "First Reply"],
              ["second", "Second Reply"],
              ["third", "Third Reply"],
              ["fourth_plus", "Fourth+ Reply"],
              ["follow_up", "Follow-Up"],
            ];
            return (
              <div className="space-y-3">
                {stageLabels.map(([key, label]) => (
                  <details key={key} className="group">
                    <summary className="cursor-pointer text-xs font-medium text-foreground flex items-center gap-2">
                      <span className="transition-transform group-open:rotate-90">&#9654;</span>
                      {label}
                      {stages[key] ? <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary">set</span> : null}
                    </summary>
                    <textarea
                      value={stages[key] ?? ""}
                      onChange={(e) => updateStage(key, e.target.value)}
                      rows={3}
                      placeholder={`Optional ${label.toLowerCase()} prompt override...`}
                      className="mt-2 w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground resize-none focus:outline-none focus:ring-2 focus:ring-ring"
                    />
                  </details>
                ))}
              </div>
            );
          })()}
        </SettingsCard>

                <SettingsCard>
          <h3 className="text-sm font-semibold text-foreground mb-4">Advanced</h3>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-sm text-foreground">Pre-scan Mode</span>
              <div className="relative">
                <button onMouseEnter={() => setShowTooltip(true)} onMouseLeave={() => setShowTooltip(false)} className="text-muted-foreground hover:text-foreground">
                  <Info className="w-4 h-4" />
                </button>
                {showTooltip && (
                  <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 w-64 p-3 bg-foreground text-background text-xs rounded-lg shadow-lg animate-fade-in z-10">
                    Profiles continuously scan groups for keyword matches and queue actions ahead of execution time.
                  </div>
                )}
              </div>
            </div>
            <Toggle checked={settings.preScanEnabled} onChange={() => updateAndSaveNow({ preScanEnabled: !settings.preScanEnabled })} size="sm" />
          </div>
        </SettingsCard>

        <SettingsCard title="Fallback Decision Trees">
          <div>
            <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">Comment Flow</h4>
            <div className="space-y-1 text-xs text-muted-foreground bg-muted/30 rounded-lg p-3">
              <p>
                1. If keyword match - <span className="text-foreground font-medium">Use rule prompt</span>
              </p>
              <p>
                2. If no keyword match - <span className="text-foreground font-medium">Use General Comment Fallback</span>
              </p>
              <p>
                3. If fallback disabled - <span className="text-foreground font-medium">Skip post</span>
              </p>
            </div>
          </div>
          <div>
            <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">DM Flow</h4>
            <div className="space-y-1 text-xs text-muted-foreground bg-muted/30 rounded-lg p-3">
              <p>
                1. If keyword rule exists - <span className="text-foreground font-medium">Use rule DM prompt</span>
              </p>
              <p>
                2. Else - <span className="text-foreground font-medium">Global Master DM Fallback</span>
              </p>
              <p>
                3. Else - <span className="text-foreground font-medium">Suggest only (no send)</span>
              </p>
            </div>
          </div>
          <p className="text-[11px] text-muted-foreground">
            All fallback levels are logged with <code className="bg-muted px-1 py-0.5 rounded text-[10px]">fallback_level_used</code>
          </p>
        </SettingsCard>

        <button onClick={() => saveSettings()} className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors">
          Save All Settings
        </button>
      </div>
    </div>
  );
}

function SettingsCard({ title, children }: { title?: string; children: React.ReactNode }) {
  return (
    <div className="bg-card border border-border rounded-xl p-5 space-y-4">
      {title && <h3 className="text-sm font-semibold text-foreground">{title}</h3>}
      {children}
    </div>
  );
}

function Toggle({ checked, onChange, size = "md" }: { checked: boolean; onChange: () => void; size?: "sm" | "md" }) {
  const isSm = size === "sm";
  return (
    <button onClick={onChange} className={`relative rounded-full transition-colors ${checked ? "bg-primary" : "bg-muted"} ${isSm ? "w-9 h-5" : "w-12 h-7"}`}>
      <span className={`absolute top-0.5 left-0.5 bg-card rounded-full shadow transition-transform ${isSm ? "w-4 h-4" : "w-6 h-6"} ${checked ? (isSm ? "translate-x-4" : "translate-x-5") : ""}`} />
    </button>
  );
}

function SettingsInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring" />;
}

function TimeInput({ id, value, onChange }: { id: string; value: string; onChange: (value: string) => void }) {
  const [parts, setParts] = useState(() => splitTime(value));
  const [hourInput, setHourInput] = useState(String(splitTime(value).hour12));
  const [minuteInput, setMinuteInput] = useState(String(splitTime(value).minute).padStart(2, "0"));

  useEffect(() => {
    const next = splitTime(value);
    setParts(next);
    setHourInput(String(next.hour12));
    setMinuteInput(String(next.minute).padStart(2, "0"));
  }, [value]);

  const commit = (hourRaw: string, minuteRaw: string, meridiem: "AM" | "PM") => {
    const hourIsNumeric = /^\d{1,2}$/.test(String(hourRaw || "").trim());
    const minuteIsNumeric = /^\d{1,2}$/.test(String(minuteRaw || "").trim());
    const parsedHour = hourIsNumeric ? Number(hourRaw) : Number.NaN;
    const parsedMinute = minuteIsNumeric ? Number(minuteRaw) : Number.NaN;
    const hour12 = Number.isFinite(parsedHour) ? Math.max(1, Math.min(12, parsedHour)) : parts.hour12;
    const minute = Number.isFinite(parsedMinute) ? Math.max(0, Math.min(59, parsedMinute)) : parts.minute;
    const next = { hour12, minute, meridiem };
    setParts(next);
    setHourInput(String(hour12));
    setMinuteInput(String(minute).padStart(2, "0"));
    onChange(buildTime(hour12, minute, meridiem));
  };

  return (
    <div id={id} className="flex items-center gap-2">
      <input
        type="text"
        inputMode="numeric"
        value={hourInput}
        onChange={(e) => {
          const next = e.target.value.replace(/\D/g, "").slice(0, 2);
          setHourInput(next);
        }}
        onBlur={() => commit(hourInput, minuteInput, parts.meridiem)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit(hourInput, minuteInput, parts.meridiem);
          }
        }}
        className="w-14 px-2 py-2 rounded-lg border border-border bg-background text-sm text-foreground text-center focus:outline-none focus:ring-2 focus:ring-ring"
        aria-label="Hour"
      />
      <span className="text-sm text-muted-foreground">:</span>
      <input
        type="text"
        inputMode="numeric"
        value={minuteInput}
        onChange={(e) => {
          const next = e.target.value.replace(/\D/g, "").slice(0, 2);
          setMinuteInput(next);
        }}
        onBlur={() => commit(hourInput, minuteInput, parts.meridiem)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit(hourInput, minuteInput, parts.meridiem);
          }
        }}
        className="w-14 px-2 py-2 rounded-lg border border-border bg-background text-sm text-foreground text-center focus:outline-none focus:ring-2 focus:ring-ring"
        aria-label="Minute"
      />
      <select
        value={parts.meridiem}
        onChange={(e) => {
          const nextMeridiem = (e.target.value === "PM" ? "PM" : "AM") as "AM" | "PM";
          commit(hourInput, minuteInput, nextMeridiem);
        }}
        className="px-2 py-2 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
        aria-label="AM PM"
      >
        <option value="AM">AM</option>
        <option value="PM">PM</option>
      </select>
      <Clock3 className="w-3.5 h-3.5 text-muted-foreground" />
    </div>
  );
}
