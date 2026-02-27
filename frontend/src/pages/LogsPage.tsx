import { useEffect, useMemo, useRef, useState } from "react";
import { useBackend } from "@/context/BackendContext";
import { api } from "@/lib/api";
import type { LogEntry } from "@/lib/types";
import { RefreshCw, Trash2 } from "lucide-react";

const statusColors: Record<string, string> = {
  success: "text-success",
  retry: "text-warning",
  error: "text-destructive",
  info: "text-primary",
};

const formatLogTime = (raw: string) => {
  const value = (raw || "").trim();
  if (!value) return "--:-- --";
  if (/(am|pm)$/i.test(value)) return value.toUpperCase();

  // Handle ISO timestamps like 2026-02-15T23:14:02.588489
  if (value.includes("T")) {
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) {
      return parsed.toLocaleTimeString("en-US", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: true,
      });
    }
  }

  const match = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(value);
  if (!match) return value;

  const h24 = Number(match[1]);
  const mm = match[2];
  const ss = match[3];
  const isPm = h24 >= 12;
  const h12 = h24 % 12 || 12;
  const core = `${String(h12).padStart(2, "0")}:${mm}${ss ? `:${ss}` : ""}`;
  return `${core} ${isPm ? "PM" : "AM"}`;
};

type LogMode = "simple" | "debug";
type LogModule = "chats" | "queue" | "profiles" | "openai" | "proxy" | "system";

const SIMPLE_HIDDEN_PATTERNS: RegExp[] = [
  /\[SKOOL\]\[TRACE\]/i,
  /\[TRACE\]/i,
  /\[SKOOL\] Due queue items:/i,
  /\[SKOOL\] Queue prefill:/i,
  /\[SKOOL\] Queue prefill started/i,
  /\[SKOOL\] Queue prefill retry round/i,
  /\[SKOOL\] Queue prefill stopped/i,
  /\[SKOOL\] Queue candidate accepted:/i,
  /\[SKOOL\] Queue candidate skipped:/i,
  /\[SKOOL\] Feed candidate /i,
  /\[SKOOL\] Community posts collected=/i,
  /\[SKOOL\] Community scan started:/i,
  /\[SKOOL\] TASK START /i,
  /\[SKOOL\] OPENING POST /i,
  /\[SKOOL\] POST OPENED /i,
  /\[SKOOL\] AI GENERATE /i,
  /\[SKOOL\] WRITING COMMENT /i,
  /\[SKOOL\] SEND CLICK /i,
  /\[SKOOL\] Queue task updated /i,
  /\[SKOOL\] Running profile /i,
  /\[SKOOL\] Round-robin mode:/i,
  /\[SKOOL\] ===== SCHEDULER LOOP STARTED =====/i,
  /\[SKOOL\] Profile '.+' outside schedule/i,
  /\[SKOOL\] All profiles are outside schedule/i,
  /\[SKOOL\] Pending due tasks /i,
  /\[SKOOL\] Rescheduled \d+ overdue queue task\(s\)/i,
];

const NORMAL_INCLUDE_PATTERNS: RegExp[] = [
  /\[SKOOL\] Scheduler started/i,
  /\[SKOOL\] Scheduler stopped/i,
  /\[SKOOL\] Scheduler paused/i,
  /\[SKOOL\] Scheduler resumed/i,
  /\[SKOOL\] Queue prefill summary:/i,
  /\[SKOOL\] Queue task added task=/i,
  /\[SKOOL\] Queue task updated task=/i,
  /\[SKOOL\] Profile pass done:/i,
  /\[SKOOL\] Comment posted task=/i,
  /\[SKOOL\] TASK SUCCESS task=/i,
  /\[SKOOL\] Task requeued task=/i,
  /\[SKOOL\] Comment failed task=/i,
  /\[SKOOL\] Inbox sync failed:/i,
  /\[SKOOL\] Inbox sync retry:/i,
  /\[SKOOL\] Inbox sync recovered:/i,
  /\[SKOOL\] Login check /i,
];

const FRIENDLY_REASON_MAP: Record<string, string> = {
  send_or_dom_error: "Failed to submit comment (UI/send error).",
  comments_turned_off_for_post: "Skipped: comments are turned off for this post.",
  membership_pending_approval: "Skipped: membership is pending approval in this community.",
  already_commented_history: "Skipped: this post was already commented before.",
  already_commented_on_thread: "Skipped: profile already commented in this thread.",
  community_not_found: "Skipped: community was not found.",
  community_paused: "Skipped: community is paused.",
  network_or_navigation_error: "Network issue while opening/submitting. Task requeued.",
  session_logged_out_on_post_open: "Session expired on post open. Please log in again.",
  empty_ai_reply: "AI returned an empty reply. Task skipped or retried.",
  archived_read_only: "Skipped: community is archived (read-only).",
  no_comment_targets: "Skipped: no comment targets found in thread.",
  no_eligible_targets: "Skipped: no eligible comment target found.",
  no_keyword_match_and_fallback_disabled: "Skipped: no keyword match and fallback is disabled.",
};

const MODULE_LABELS: Record<LogModule, string> = {
  chats: "Chats",
  queue: "Queue",
  profiles: "Profiles",
  openai: "OpenAI",
  proxy: "Proxy",
  system: "System",
};

function inferModuleAction(rawMessage: string): { module: LogModule; action: string } {
  const message = String(rawMessage || "").trim();
  const lower = message.toLowerCase();

  if (lower.includes("openai") || lower.includes("api key") || lower.includes("ai auto")) {
    if (lower.includes("check") || lower.includes("test") || lower.includes("connection")) return { module: "openai", action: "check_key" };
    if (lower.includes("set") || lower.includes("save") || lower.includes("update")) return { module: "openai", action: "set_key" };
    return { module: "openai", action: "event" };
  }
  if (lower.includes("proxy")) {
    if (lower.includes("check") || lower.includes("passed") || lower.includes("failed") || lower.includes("retry")) return { module: "proxy", action: "check" };
    if (lower.includes("cache") || lower.includes("cached")) return { module: "proxy", action: "cache" };
    return { module: "proxy", action: "event" };
  }
  if (lower.includes("chat") || lower.includes("inbox sync") || lower.includes("dm send") || lower.includes("conversation")) {
    if (lower.includes("send")) return { module: "chats", action: "send" };
    if (lower.includes("sync") || lower.includes("imported") || lower.includes("complete")) return { module: "chats", action: "sync" };
    if (lower.includes("fetch") || lower.includes("load")) return { module: "chats", action: "fetch" };
    if (lower.includes("retry")) return { module: "chats", action: "retry" };
    if (lower.includes("failed") || lower.includes("error")) return { module: "chats", action: "fail" };
    return { module: "chats", action: "event" };
  }
  if (lower.includes("queue") || lower.includes("task=")) {
    if (lower.includes("added") || lower.includes("enqueued")) return { module: "queue", action: "add" };
    if (lower.includes("updated") || lower.includes("expedited")) return { module: "queue", action: "update" };
    if (lower.includes("removed") || lower.includes("delete")) return { module: "queue", action: "remove" };
    if (lower.includes("requeued")) return { module: "queue", action: "requeue" };
    if (lower.includes("start") || lower.includes("execute")) return { module: "queue", action: "execute" };
    return { module: "queue", action: "event" };
  }
  if (lower.includes("profile") || lower.includes("login check") || lower.includes("session") || lower.includes("scheduler")) {
    if (lower.includes("check")) return { module: "profiles", action: "check" };
    if (lower.includes("run") || lower.includes("pass")) return { module: "profiles", action: "run" };
    if (lower.includes("pause")) return { module: "profiles", action: "pause" };
    if (lower.includes("resume")) return { module: "profiles", action: "resume" };
    return { module: "profiles", action: "event" };
  }
  return { module: "system", action: "event" };
}

function getLogModule(log: LogEntry): LogModule {
  const raw = String(log.module || "").trim().toLowerCase();
  if (raw === "chats" || raw === "queue" || raw === "profiles" || raw === "openai" || raw === "proxy" || raw === "system") {
    return raw;
  }
  return inferModuleAction(log.message).module;
}

function getLogAction(log: LogEntry): string {
  const raw = String(log.action || "").trim().toLowerCase();
  if (raw) return raw;
  return inferModuleAction(log.message).action;
}

function simplifyLogMessage(rawMessage: string): string {
  const message = (rawMessage || "").trim();
  if (!message) return "No details.";

  if (/\[SKOOL\] Scheduler started/i.test(message)) return "Automation started.";
  if (/\[SKOOL\] Scheduler stopped/i.test(message)) return "Automation stopped.";
  if (/\[SKOOL\] Scheduler paused/i.test(message)) return "Automation paused.";
  if (/\[SKOOL\] Scheduler resumed/i.test(message)) return "Automation resumed.";
  const inboxSyncStartMatch = message.match(/Inbox sync started: checking (\d+)\/(\d+) profile\(s\)(?: \[(.+)\])?\./i);
  if (inboxSyncStartMatch) {
    return `Inbox sync started: checking ${inboxSyncStartMatch[1]}/${inboxSyncStartMatch[2]} profile(s).`;
  }
  const inboxSyncProfileDoneMatch = message.match(/Inbox sync complete:\s+(\d+)\s+chat\(s\)\s+imported\s+for\s+this\s+profile\s+in\s+([0-9.]+)s\./i);
  if (inboxSyncProfileDoneMatch) {
    return `Inbox sync complete: ${inboxSyncProfileDoneMatch[1]} chat(s) imported.`;
  }
  const inboxSyncGlobalDoneMatch = message.match(/Inbox sync complete:\s+active chats=(\d+),\s+stale removed=(\d+),\s+deduped=(\d+),\s+orphan removed=(\d+)\./i);
  if (inboxSyncGlobalDoneMatch) {
    return `Inbox sync finished: active chats ${inboxSyncGlobalDoneMatch[1]}, deduped ${inboxSyncGlobalDoneMatch[3]}.`;
  }
  if (/Inbox sync skipped:/i.test(message)) {
    return message;
  }

  const queueSummaryMatch = message.match(/\[SKOOL\] Queue prefill summary: added=(\d+)\/(\d+)\s+queued_now=(\d+)(?:\s+queued_total=(\d+))?/i);
  if (queueSummaryMatch) {
    const added = Number(queueSummaryMatch[1] || 0);
    const queuedNow = Number(queueSummaryMatch[3] || 0);
    const queuedTotal = Number(queueSummaryMatch[4] || 0);
    const queueLabel = queuedTotal > 0 ? queuedTotal : queuedNow;
    if (added > 0) return `Added ${added} task(s) to queue. Queue now: ${queueLabel}.`;
    return `Queue scan completed. Queue now: ${queueLabel}.`;
  }
  const communityDailyLimitMatch = message.match(/\[SKOOL\] Queue prefill skipped:\s+community_daily_limit_reached\s+community=(.+?)\s+actions_today=(\d+)(?:\s+queued_today=\d+)?\s+limit=(\d+)/i);
  if (communityDailyLimitMatch) {
    const community = String(communityDailyLimitMatch[1] || "community").trim();
    return `No new tasks: ${community} reached daily limit (${communityDailyLimitMatch[2]}/${communityDailyLimitMatch[3]}).`;
  }
  const prefillStopMatch = message.match(/\[SKOOL\] Queue prefill stopped early: reason=([^\s]+)\s+queued=(\d+)\s+pass_added=(\d+)\/(\d+)/i);
  if (prefillStopMatch) {
    const reason = String(prefillStopMatch[1] || "").toLowerCase();
    if (reason === "per_pass_limit_reached") {
      return `Queue prefill reached per-pass limit (${prefillStopMatch[3]}/${prefillStopMatch[4]}).`;
    }
    if (reason === "daily_quota_reached") {
      return `Queue prefill stopped: daily profile quota reached.`;
    }
    return `Queue prefill stopped early: ${reason.replaceAll("_", " ")}.`;
  }
  const dailyLimitTraceMatch = message.match(/\[SKOOL\]\[TRACE\]\s+Community skipped:\s+daily_limit_reached\s+name=(.+?)\s+actions_today=(\d+)(?:\s+queued_today=\d+)?\s+limit=(\d+)/i);
  if (dailyLimitTraceMatch) {
    const community = String(dailyLimitTraceMatch[1] || "community").trim();
    return `No new tasks: ${community} reached daily limit (${dailyLimitTraceMatch[2]}/${dailyLimitTraceMatch[3]}).`;
  }

  const profileBackoffMatch = message.match(/\[SKOOL\] Profile network backoff applied:\s+streak=(\d+)\s+cooldown=(\d+)s/i);
  if (profileBackoffMatch) {
    return `Temporary network issue. Cooling down for ${profileBackoffMatch[2]}s (attempt ${profileBackoffMatch[1]}).`;
  }
  const profileCooldownMatch = message.match(/\[SKOOL\] Profile temporary network cooldown:\s+(\d+)s/i);
  if (profileCooldownMatch) {
    return `Waiting for network cooldown: ${profileCooldownMatch[1]}s.`;
  }

  const profilePassMatch = message.match(/\[SKOOL\] Profile pass done: posted=(\d+)\s+skipped=(\d+)\s+blacklisted=(\d+)/i);
  if (profilePassMatch) {
    return `Profile pass done: posted ${profilePassMatch[1]}, skipped ${profilePassMatch[2]}, blacklisted ${profilePassMatch[3]}.`;
  }

  const taskStartMatch = message.match(/\[SKOOL\] TASK START task=([^\s]+)/i);
  if (taskStartMatch) return `Started task ${taskStartMatch[1]}.`;
  const taskSuccessMatch = message.match(/\[SKOOL\] TASK SUCCESS task=([^\s]+)/i);
  if (taskSuccessMatch) return `Task ${taskSuccessMatch[1]} completed successfully.`;
  const commentPostedMatch = message.match(/\[SKOOL\] Comment posted task=([^\s]+)/i);
  if (commentPostedMatch) return `Comment posted successfully (task ${commentPostedMatch[1]}).`;
  const queueAddedMatch = message.match(/\[SKOOL\] Queue task added task=([^\s]+)\s+community=(.+?)\s+scheduled=(.+)$/i);
  if (queueAddedMatch) {
    const community = (queueAddedMatch[2] || "").trim();
    const scheduled = (queueAddedMatch[3] || "").trim();
    return `Queue task added for ${community || "community"} at ${scheduled}.`;
  }
  const requeueMatch = message.match(/\[SKOOL\] Task requeued task=([^\s]+)\s+reason=([^\s]+)/i);
  if (requeueMatch) {
    const reason = requeueMatch[2];
    return `Task ${requeueMatch[1]} requeued: ${FRIENDLY_REASON_MAP[reason] || reason.replaceAll("_", " ")}.`;
  }

  const commentFailedMatch = message.match(/\[SKOOL\] Comment failed task=([^\s]+)\s+reason=([^\s]+)/i);
  if (commentFailedMatch) {
    const task = commentFailedMatch[1];
    const reason = commentFailedMatch[2];
    return `Task ${task} failed: ${FRIENDLY_REASON_MAP[reason] || reason.replaceAll("_", " ")}.`;
  }

  if (/\[SKOOL\] OPENING POST/i.test(message)) return "Opening post...";
  if (/\[SKOOL\] POST OPENED/i.test(message)) return "Post opened.";
  if (/\[SKOOL\] AI GENERATE/i.test(message)) return "Generating comment...";
  if (/\[SKOOL\] WRITING COMMENT/i.test(message)) return "Writing comment...";
  if (/\[SKOOL\] SEND CLICK/i.test(message)) return "Submitting comment...";
  if (/\[SKOOL\] Comment retry/i.test(message)) return "Retrying comment submission...";

  const cleaned = message.replace(/\[SKOOL\]\s*/gi, "").trim();
  return cleaned;
}

function shouldHideInSimpleMode(message: string): boolean {
  return SIMPLE_HIDDEN_PATTERNS.some((pattern) => pattern.test(message));
}

function shouldShowInNormalMode(log: LogEntry): boolean {
  const message = String(log.message || "");
  const status = String(log.status || "").toLowerCase();
  const logModule = getLogModule(log);
  if (status === "error" || status === "retry") return true;
  if (shouldHideInSimpleMode(message)) return false;
  if (logModule === "chats" || logModule === "queue" || logModule === "profiles" || logModule === "openai" || logModule === "proxy") {
    return true;
  }
  return NORMAL_INCLUDE_PATTERNS.some((pattern) => pattern.test(message));
}

function normalizeMessageForMode(log: LogEntry, mode: LogMode): string {
  if (mode === "debug") return String(log.message || "");
  const simplified = simplifyLogMessage(String(log.message || ""));
  if (simplified) return simplified;
  const module = getLogModule(log);
  const action = getLogAction(log);
  if (module === "chats" && action === "sync") return "Chat sync updated.";
  if (module === "chats" && action === "start") return "Chat sync started.";
  if (module === "profiles" && action === "run") return "Profile cycle completed.";
  if (module === "queue" && action === "prefill") return "Queue prefill event.";
  if (log.status === "error") return "Operation failed. Open Debug mode for technical details.";
  if (log.status === "retry") return "Temporary issue detected. System will retry automatically.";
  if (log.status === "success") return "Operation completed successfully.";
  return "System event.";
}

export default function LogsPage() {
  const { logs: backendLogs, profiles, refreshLogs } = useBackend();
  const [filterProfile, setFilterProfile] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [filterModule, setFilterModule] = useState("");
  const [mode, setMode] = useState<LogMode>(() => {
    const saved = window.localStorage.getItem("logs_mode");
    return saved === "debug" ? "debug" : "simple";
  });
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isClearing, setIsClearing] = useState(false);
  const [renderLimit, setRenderLimit] = useState(150);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    window.localStorage.setItem("logs_mode", mode);
  }, [mode]);

  const filtered = useMemo(() => backendLogs.filter((log) => {
    if (filterProfile && log.profile !== filterProfile) return false;
    if (filterStatus && log.status !== filterStatus) return false;
    if (filterModule && getLogModule(log) !== filterModule) return false;
    if (mode === "simple" && !shouldShowInNormalMode(log)) return false;
    return true;
  }), [backendLogs, filterProfile, filterStatus, filterModule, mode]);

  useEffect(() => {
    setRenderLimit(150);
  }, [filterProfile, filterStatus, filterModule, mode]);

  const visibleLogs = useMemo(() => filtered.slice(0, renderLimit), [filtered, renderLimit]);

  const handleRefreshLogs = async () => {
    setIsRefreshing(true);
    try {
      await refreshLogs();
    } finally {
      setIsRefreshing(false);
    }
  };

  const handleClearLogs = async () => {
    const ok = window.confirm("Clear all logs?");
    if (!ok) return;
    setIsClearing(true);
    try {
      await api.clearLogs();
      await refreshLogs();
    } finally {
      setIsClearing(false);
    }
  };

  return (
    <div className="p-4 md:p-6 lg:p-8 pt-16 md:pt-6 lg:pt-8 max-w-7xl h-screen flex flex-col">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-foreground">Logs</h1>
        <p className="text-sm text-muted-foreground mt-1">
          {mode === "debug"
            ? "Debug mode: full technical log stream."
            : "Normal mode: clear non-technical event feed."}
        </p>
      </div>

      <div className="flex gap-3 mb-4 items-center">
        <select value={filterProfile} onChange={(e) => setFilterProfile(e.target.value)} className="text-xs px-3 py-2 rounded-lg border border-border bg-background text-foreground min-w-[140px]">
          <option value="">All Profiles</option>
          {profiles.map((p) => <option key={p.id} value={p.name}>{p.name}</option>)}
        </select>
        <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)} className="text-xs px-3 py-2 rounded-lg border border-border bg-background text-foreground min-w-[120px]">
          <option value="">All Statuses</option>
          <option value="success">Success</option>
          <option value="retry">Retry</option>
          <option value="error">Error</option>
          <option value="info">Info</option>
        </select>
        <select value={filterModule} onChange={(e) => setFilterModule(e.target.value)} className="text-xs px-3 py-2 rounded-lg border border-border bg-background text-foreground min-w-[120px]">
          <option value="">All Modules</option>
          <option value="chats">Chats</option>
          <option value="queue">Queue</option>
          <option value="profiles">Profiles</option>
          <option value="openai">OpenAI</option>
          <option value="proxy">Proxy</option>
          <option value="system">System</option>
        </select>
        <div className="inline-flex items-center rounded-lg border border-border bg-background p-0.5">
          <button
            onClick={() => setMode("simple")}
            className={`px-2.5 py-1.5 text-xs rounded-md transition-colors ${mode === "simple" ? "bg-primary text-primary-foreground" : "text-foreground hover:bg-muted"}`}
          >
            Normal
          </button>
          <button
            onClick={() => setMode("debug")}
            className={`px-2.5 py-1.5 text-xs rounded-md transition-colors ${mode === "debug" ? "bg-primary text-primary-foreground" : "text-foreground hover:bg-muted"}`}
          >
            Debug
          </button>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={handleRefreshLogs}
            disabled={isRefreshing || isClearing}
            className="inline-flex items-center gap-1.5 text-xs px-3 py-2 rounded-lg border border-border bg-background text-foreground hover:bg-muted disabled:opacity-60"
            title="Refresh logs"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? "animate-spin" : ""}`} />
            Refresh
          </button>
          <button
            onClick={handleClearLogs}
            disabled={isClearing || isRefreshing}
            className="inline-flex items-center gap-1.5 text-xs px-3 py-2 rounded-lg border border-destructive/50 bg-background text-destructive hover:bg-destructive/10 disabled:opacity-60"
            title="Clear all logs"
          >
            <Trash2 className="w-3.5 h-3.5" />
            Clear All
          </button>
        </div>
      </div>

      <div className="flex-1 bg-foreground rounded-xl overflow-hidden min-h-0 border border-border/30">
        <div ref={scrollRef} className="h-full overflow-y-auto font-mono text-xs p-4 space-y-0.5">
          {visibleLogs.map((log) => (
            <div key={log.id} className="flex gap-3 py-1">
              <span className="text-muted-foreground/60 shrink-0 w-24">{formatLogTime(log.timestamp)}</span>
              <span className={`shrink-0 w-16 uppercase font-semibold ${statusColors[log.status] || statusColors.info}`}>{log.status}</span>
              <span className="text-cyan-300 shrink-0 w-16 uppercase font-semibold">{MODULE_LABELS[getLogModule(log)]}</span>
              <span className="text-amber-300 shrink-0 w-20 uppercase font-semibold">{getLogAction(log)}</span>
              <span className="text-primary-foreground/70 shrink-0 w-28 truncate">{log.profile}</span>
              <span className="text-primary-foreground/90 flex-1 break-words">
                {normalizeMessageForMode(log, mode)}
              </span>
              {mode === "debug" && log.fallbackLevelUsed && (
                <span className="text-warning/80 shrink-0 text-[10px]">[{log.fallbackLevelUsed}]</span>
              )}
            </div>
          ))}
          {filtered.length > visibleLogs.length && (
            <div className="pt-3">
              <button
                onClick={() => setRenderLimit((prev) => prev + 150)}
                className="text-xs px-3 py-1.5 rounded-md border border-border bg-background text-foreground hover:bg-muted"
              >
                Load more ({filtered.length - visibleLogs.length} left)
              </button>
            </div>
          )}
          {filtered.length === 0 && <div className="px-1 py-2 text-sm text-primary-foreground/60">No logs found for selected filters.</div>}
        </div>
      </div>
    </div>
  );
}
