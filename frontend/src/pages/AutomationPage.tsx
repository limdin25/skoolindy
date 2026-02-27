import { useEffect, useRef, useState } from "react";
import { Clock3, Download, Info, Loader2, Play, Square, Upload, RotateCcw } from "lucide-react";
import { useAutomationSettings } from "@/hooks/useEngageFlow";
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

export default function AutomationPage() {
  const settingsQuery = useAutomationSettings();
  const { engineStatus, refresh } = useBackend();
  const [settings, setSettings] = useState<AutomationSettings | null>(settingsQuery.data ?? null);
  const [showTooltip, setShowTooltip] = useState(false);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [actionPending, setActionPending] = useState<"start" | "resume" | "stop" | null>(null);
  const [resetTasksPending, setResetTasksPending] = useState(false);
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
  const resetTasksDisabledReason = "Stop automation to clear queued tasks.";
  const configuredRestRounds = Math.max(1, Number(settings.roundsBeforeConnectionRest || 1));
  const configuredRestMinutes = Math.max(1, Number(settings.connectionRestMinutes || 1));

  return (
    <div className="p-4 md:p-6 lg:p-8 pt-16 md:pt-6 lg:pt-8 max-w-2xl">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-foreground">Automation Settings</h1>
        <p className="text-sm text-muted-foreground mt-1">Global configuration inherited by all profiles</p>
      </div>

      <div className="space-y-6">
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
                      {resetTasksDisabledReason}
                    </TooltipContent>
                  )}
                </Tooltip>
              </TooltipProvider>
            </div>
          </div>
        </SettingsCard>

        <SettingsCard>
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold text-foreground">Master Automation</h3>
              <p className="text-xs text-muted-foreground mt-0.5">{settings.masterEnabled ? "All automation is active" : "All automation is paused"}</p>
            </div>
            <Toggle checked={settings.masterEnabled} onChange={() => updateAndSaveNow({ masterEnabled: !settings.masterEnabled })} />
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
            <label className="text-xs text-muted-foreground mb-1 block">Max Tasks Per Profile Per Round</label>
            <p className="text-[11px] text-muted-foreground mb-1.5">
              Queue prefill limit per profile for one scheduler pass (your current default is 2)
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
              <label className="text-xs text-muted-foreground mb-1 block">Rounds Before Connection Rest</label>
              <p className="text-[11px] text-muted-foreground mb-1.5 min-h-[2rem]">After this many full circles, automation pauses connection activity.</p>
              <SettingsInput
                type="number"
                min={1}
                value={settings.roundsBeforeConnectionRest}
                onChange={(e) =>
                  update({
                    roundsBeforeConnectionRest: Math.max(1, toSafeNumber(e.target.value, settings.roundsBeforeConnectionRest)),
                  })
                }
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">Connection Rest (minutes)</label>
              <p className="text-[11px] text-muted-foreground mb-1.5 min-h-[2rem]">How long to pause after each connection-rest trigger.</p>
              <SettingsInput
                type="number"
                min={1}
                value={settings.connectionRestMinutes}
                onChange={(e) =>
                  update({
                    connectionRestMinutes: Math.max(1, toSafeNumber(e.target.value, settings.connectionRestMinutes)),
                  })
                }
              />
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
          <div className="rounded-lg border border-border bg-muted/20 px-3 py-2">
            <p className="text-[11px] text-muted-foreground">
              Current setup: pause after <span className="text-foreground font-medium">{configuredRestRounds}</span> rounds
              for <span className="text-foreground font-medium">{configuredRestMinutes}</span> minutes.
            </p>
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
