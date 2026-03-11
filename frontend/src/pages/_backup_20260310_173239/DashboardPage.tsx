import { useEffect, useMemo, useRef, useState } from "react";
import { Users, MessageSquare, Sparkles, Clock, Activity, ChevronDown, ChevronUp, ExternalLink, Download, Key, CheckCircle, XCircle, Loader2 } from "lucide-react";
import { useActivity, useAutomationSettings, useCommunities, useProfiles, useQueue, useQueuePreview } from "@/hooks/useEngageFlow";
import { ApiError, api } from "@/lib/api";
import NextActionsDrawer from "@/components/NextActionsDrawer";
import { useBackend } from "@/context/BackendContext";
import { toast } from "sonner";
import type { LogEntry, QueueItem } from "@/lib/types";
const UK_TIMEZONE = "Europe/London";
const SERVER_TIMEZONE = "Europe/Berlin";

const getDatePartsInTz = (date: Date, timeZone: string): Record<string, number> => {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const map: Record<string, number> = {};
  for (const p of parts) {
    if (p.type !== "literal") map[p.type] = Number(p.value);
  }
  return map;
};

const zonedToEpoch = (
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
  second: number,
  timeZone: string,
): number => {
  const baseUtc = Date.UTC(year, month - 1, day, hour, minute, second);
  let guess = baseUtc;
  for (let i = 0; i < 2; i += 1) {
    const tzParts = getDatePartsInTz(new Date(guess), timeZone);
    const asUtc = Date.UTC(
      Number(tzParts.year || 0),
      Number(tzParts.month || 1) - 1,
      Number(tzParts.day || 1),
      Number(tzParts.hour || 0),
      Number(tzParts.minute || 0),
      Number(tzParts.second || 0),
    );
    const offset = asUtc - guess;
    const adjusted = baseUtc - offset;
    if (adjusted === guess) break;
    guess = adjusted;
  }
  return guess;
};

const parseServerTimestamp = (value: string): number => {
  const text = String(value || "").trim();
  if (!text) return Number.NaN;
  const hasTimezone = /(?:Z|[+-]\d{2}:\d{2})$/i.test(text);
  const looksIsoNoZone = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(text);
  if (hasTimezone) {
    const ts = Date.parse(text);
    return Number.isNaN(ts) ? Number.NaN : ts;
  }
  if (looksIsoNoZone) {
    const norm = text.replace(" ", "T");
    const m = norm.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/);
    if (!m) return Number.NaN;
    return zonedToEpoch(
      Number(m[1]),
      Number(m[2]),
      Number(m[3]),
      Number(m[4]),
      Number(m[5]),
      Number(m[6]),
      SERVER_TIMEZONE,
    );
  }
  const hms = text.match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?$/);
  if (hms) {
    const nowParts = getDatePartsInTz(new Date(), SERVER_TIMEZONE);
    return zonedToEpoch(
      Number(nowParts.year || 0),
      Number(nowParts.month || 1),
      Number(nowParts.day || 1),
      Number(hms[1]),
      Number(hms[2]),
      Number(hms[3] || 0),
      SERVER_TIMEZONE,
    );
  }
  const ts = Date.parse(text);
  return Number.isNaN(ts) ? Number.NaN : ts;
};

function StatCard({ icon: Icon, label, value, sub, color }: { icon: any; label: string; value: string | number; sub?: string; color: string }) {
  return (
    <div className="bg-card border border-border rounded-xl p-5 animate-count-up">
      <div className="flex items-center gap-3 mb-3">
        <div className={`flex items-center justify-center w-10 h-10 rounded-lg ${color}`}>
          <Icon className="w-5 h-5" />
        </div>
        <span className="text-sm font-medium text-muted-foreground">{label}</span>
      </div>
      <p className="text-2xl font-bold text-foreground">{value}</p>
      {sub && <p className="text-xs text-muted-foreground mt-1">{sub}</p>}
    </div>
  );
}

const isMaskedOpenAiKey = (value: string) => value.includes("*") || value.includes("...") || value.includes("вЂ¦");

function formatRelativeTime(raw: string) {
  const ts = parseServerTimestamp(String(raw || ""));
  if (!Number.isFinite(ts)) return String(raw || "").trim() || "just now";
  const deltaSec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (deltaSec < 60) return "just now";
  const min = Math.floor(deltaSec / 60);
  if (min < 60) return `${min} min ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} hr ago`;
  const days = Math.floor(hr / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

function prettifyGroupName(rawGroupName: string, postUrl: string) {
  const raw = String(rawGroupName || "").trim();
  const source = raw || String(postUrl || "").trim();
  if (!source) return "Skool";
  if (source.startsWith("http://") || source.startsWith("https://")) {
    try {
      const parsed = new URL(source);
      const slug = parsed.pathname.replace(/^\/+|\/+$/g, "").split("/")[0] || parsed.hostname;
      return slug
        .split("-")
        .filter(Boolean)
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(" ");
    } catch {
      return source;
    }
  }
  return raw;
}

function parseQueueTimestampMs(scheduledFor: string, scheduledTime: string, nowMs: number) {
  const rawScheduledFor = String(scheduledFor || "").trim();
  if (rawScheduledFor) {
    const hasTimezone = /(?:Z|[+-]\d{2}:\d{2})$/i.test(rawScheduledFor);
    const looksIsoNoZone = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(rawScheduledFor);
    const normalized = hasTimezone
      ? rawScheduledFor
      : looksIsoNoZone
        ? rawScheduledFor.replace(" ", "T")
        : rawScheduledFor;
    const ts = Date.parse(normalized);
    if (Number.isFinite(ts)) return ts;
  }
  const t = String(scheduledTime || "").trim();
  const tm = t.match(/^(\d{1,2}):(\d{2})(?:\s*(AM|PM))?$/i);
  if (!tm) return Number.NaN;
  let hour = Number(tm[1]);
  const minute = Number(tm[2]);
  const meridiem = String(tm[3] || "").toUpperCase();
  if (meridiem === "PM" && hour < 12) hour += 12;
  if (meridiem === "AM" && hour === 12) hour = 0;
  const anchor = new Date(nowMs);
  let localTs = new Date(anchor.getFullYear(), anchor.getMonth(), anchor.getDate(), hour, minute, 0, 0).getTime();
  // If only HH:mm is available and today's slot already passed, assume next day.
  if (localTs <= nowMs) {
    localTs += 24 * 60 * 60 * 1000;
  }
  return localTs;
}

function interleaveQueueByProfile(items: QueueItem[]) {
  const byProfile = new Map<string, QueueItem[]>();
  const profileOrder: string[] = [];
  for (const item of items) {
    const key = String(item.profileId || item.profile || "");
    if (!byProfile.has(key)) {
      byProfile.set(key, []);
      profileOrder.push(key);
    }
    byProfile.get(key)!.push(item);
  }
  const out: QueueItem[] = [];
  while (true) {
    let tookAny = false;
    for (const key of profileOrder) {
      const bucket = byProfile.get(key);
      if (!bucket || bucket.length === 0) continue;
      const nextItem = bucket.shift();
      if (!nextItem) continue;
      out.push(nextItem);
      tookAny = true;
    }
    if (!tookAny) break;
  }
  return out;
}

function formatQueueEta(
  scheduledFor: string,
  scheduledTime: string,
  nowMs: number,
  isSchedulerRunning: boolean,
  isWaitingSlot: boolean,
  restRemainingSeconds: number,
) {
  if (!isSchedulerRunning) return "Waiting for start";
  if (restRemainingSeconds > 0) {
    const m = Math.floor(restRemainingSeconds / 60);
    const sec = Math.max(0, restRemainingSeconds % 60);
    return `Rest ${m}:${sec.toString().padStart(2, "0")}`;
  }
  if (isWaitingSlot) return "Waiting current task";
  const ts = parseQueueTimestampMs(scheduledFor, scheduledTime, nowMs);
  if (!Number.isFinite(ts)) return "Unknown time";
  const diffSec = Math.floor((ts - nowMs) / 1000);
  if (diffSec <= 0) return "Starting...";
  if (diffSec < 60) return `in ${diffSec}s`;
  if (diffSec < 3600) return `in ${Math.ceil(diffSec / 60)} min`;
  if (diffSec < 86400) return `in ${Math.ceil(diffSec / 3600)} hr`;
  return `in ${Math.ceil(diffSec / 86400)} day`;
}

type ActiveQueueTask = {
  taskId: string;
  profile: string;
  stage: string;
  startedAtMs: number;
};

type RequeueTask = {
  taskId: string;
  reason: string;
  delaySeconds: number;
  startedAtMs: number;
};

function extractTaskId(message: string): string | null {
  const m = String(message || "").match(/\btask=([^\s]+)/i);
  return m ? String(m[1] || "").trim() || null : null;
}

function parseLogTimeToMs(raw: string, nowMs: number): number {
  const text = String(raw || "").trim();
  const m = text.match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?$/);
  if (!m) return nowMs;
  const now = new Date(nowMs);
  const parsed = new Date(nowMs);
  parsed.setHours(Number(m[1]), Number(m[2]), Number(m[3] || "0"), 0);
  // Logs carry only HH:MM:SS; around midnight this can point to the future.
  if ((parsed.getTime() - now.getTime()) > (5 * 60 * 1000)) {
    parsed.setDate(parsed.getDate() - 1);
  }
  return parsed.getTime();
}

function deriveActiveQueueTask(logs: LogEntry[], nowMs: number): ActiveQueueTask | null {
  let active: ActiveQueueTask | null = null;
  const chronological = [...logs].reverse();
  let lastSchedulerStartMs = Number.NEGATIVE_INFINITY;
  for (const log of chronological) {
    const message = String(log.message || "");
    if (message.includes("[SKOOL] Scheduler started")) {
      lastSchedulerStartMs = parseLogTimeToMs(String(log.timestamp || ""), nowMs);
    }
  }
  for (const log of chronological) {
    const message = String(log.message || "");
    const logMs = parseLogTimeToMs(String(log.timestamp || ""), nowMs);
    if (logMs < lastSchedulerStartMs) continue;
    const taskId = extractTaskId(message);
    if (!taskId) continue;

    if (message.includes("TASK START")) {
      active = {
        taskId,
        profile: String(log.profile || ""),
        stage: "Opening post",
        startedAtMs: logMs,
      };
      continue;
    }
    if (!active || active.taskId !== taskId) continue;
    if (message.includes("AI GENERATE")) {
      active.stage = "Generating reply";
      continue;
    }
    if (message.includes("WRITING COMMENT")) {
      active.stage = "Writing comment";
      continue;
    }
    if (message.includes("SEND CLICK")) {
      active.stage = "Publishing comment";
      continue;
    }
    if (
      message.includes("TASK SUCCESS") ||
      message.includes("Comment failed task=") ||
      message.includes("Task requeued task=") ||
      message.includes("Task postponed task=")
    ) {
      active = null;
      continue;
    }
  }
  if (!active) return null;
  // Real "writing/publishing" should finish quickly; if it lingers, treat as stale UI state.
  if ((nowMs - active.startedAtMs) > 75 * 1000) return null;
  return active;
}

function deriveRecentRequeueTask(logs: LogEntry[], nowMs: number): RequeueTask | null {
  const requeueRx = /Task requeued task=([^\s]+)\s+reason=([^\s]+)(?:\s+delay=(\d+)s)?/i;
  let latest: RequeueTask | null = null;

  for (const log of logs) {
    const message = String(log.message || "");
    const match = message.match(requeueRx);
    if (!match) continue;
    const ts = parseLogTimeToMs(String(log.timestamp || ""), nowMs);
    const candidate: RequeueTask = {
      taskId: String(match[1] || "").trim(),
      reason: String(match[2] || "").trim(),
      delaySeconds: Number(match[3] || 0),
      startedAtMs: ts,
    };
    if (!latest || candidate.startedAtMs > latest.startedAtMs) {
      latest = candidate;
    }
  }

  if (!latest) return null;
  if ((nowMs - latest.startedAtMs) > 5 * 60 * 1000) return null;

  const taskSignalRx = new RegExp(`task=${latest.taskId}\\b`, "i");
  for (const log of logs) {
    const ts = parseLogTimeToMs(String(log.timestamp || ""), nowMs);
    if (ts <= latest.startedAtMs) continue;
    const message = String(log.message || "");
    if (!taskSignalRx.test(message)) continue;
    if (message.includes("TASK START") || message.includes("TASK SUCCESS") || message.includes("Comment failed task=")) {
      return null;
    }
  }

  return latest;
}

export default function DashboardPage() {
  const [queueExpanded, setQueueExpanded] = useState(false);
  const [activityExpanded, setActivityExpanded] = useState(false);
  const [activityFilterProfile, setActivityFilterProfile] = useState("");
  const [showNextActions, setShowNextActions] = useState(false);
  const [openaiKey, setOpenaiKey] = useState("");
  const [keyTestStatus, setKeyTestStatus] = useState<"idle" | "testing" | "success" | "error">("idle");
  const [queueRoundSavePending, setQueueRoundSavePending] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [queueTimerPauseOffsetMs, setQueueTimerPauseOffsetMs] = useState(0);
  const restTrackingRef = useRef<{ active: boolean; remainingSeconds: number }>({
    active: false,
    remainingSeconds: 0,
  });

  const profilesQuery = useProfiles();
  const activityQuery = useActivity();
  const communitiesQuery = useCommunities();
  const queueQuery = useQueue();
  const queuePreviewQuery = useQueuePreview();
  const automationSettingsQuery = useAutomationSettings();
  const { conversations, engineStatus, logs } = useBackend();

  const profiles = profilesQuery.data ?? [];
  const communities = communitiesQuery.data ?? [];
  const activityFeed = activityQuery.data ?? [];
  const queue = queueQuery.data ?? [];
  const queuePreview = queuePreviewQuery.data ?? [];
  const adjustedQueueNowMs = Math.max(0, nowMs - queueTimerPauseOffsetMs);
  const activeTask = useMemo(() => deriveActiveQueueTask(logs, nowMs), [logs, nowMs]);
  const displayActiveTask = activeTask && (nowMs - activeTask.startedAtMs) <= 20 * 1000 ? activeTask : null;
  const recentRequeueTask = useMemo(() => deriveRecentRequeueTask(logs, nowMs), [logs, nowMs]);
  const visibleQueue = queue.length > 0 ? queue : queuePreview;
  const displayQueue = interleaveQueueByProfile(visibleQueue);
  const parsedQueue = visibleQueue
    .map((item) => ({
      item,
      ts: parseQueueTimestampMs(String(item.scheduledFor || ""), String(item.scheduledTime || ""), adjustedQueueNowMs),
    }))
    .filter((entry) => Number.isFinite(entry.ts));
  const settings = automationSettingsQuery.data;
  const isEngineRunning = !!engineStatus?.isRunning && !engineStatus?.isPaused;
  const isWaitingSchedule = isEngineRunning && String(engineStatus?.runState || "") === "waiting_schedule";

  const activeProfiles = profiles.filter((p) => p.status === "running" || p.status === "ready").length;
  const messagesCount = conversations.reduce((acc, conv) => acc + conv.messages.filter((m) => !m.isDeletedUi).length, 0);
  const keywordMatches = visibleQueue.length;
  const nextQueueItem = (() => {
    const nearest = parsedQueue.sort((a, b) => a.ts - b.ts);
    return nearest.length > 0 ? nearest[0].item : displayQueue[0];
  })();
  const nextQueueTs = (() => {
    if (!nextQueueItem) return Number.NaN;
    return parseQueueTimestampMs(
      String(nextQueueItem.scheduledFor || ""),
      String(nextQueueItem.scheduledTime || ""),
      adjustedQueueNowMs,
    );
  })();
  const queueExecutionWindowActive = isEngineRunning && Number.isFinite(nextQueueTs) && (nextQueueTs - adjustedQueueNowMs) <= 10000;
  const connectionRest = engineStatus?.connectionRest;
  const emptyQueueReason = (() => {
    if (visibleQueue.length > 0) return null;

    const profileCap = Number(settings?.globalDailyCapPerAccount ?? 0);
    if (profileCap > 0) {
      const eligibleProfiles = profiles.filter((p) => p.status !== "paused");
      if (
        eligibleProfiles.length > 0 &&
        eligibleProfiles.every((p) => Number(p.dailyUsage || 0) >= profileCap)
      ) {
        return "Profile cap reached";
      }
    }

    const activeCommunities = communities.filter((c) => String(c.status || "").toLowerCase() !== "paused");
    if (
      activeCommunities.length > 0 &&
      activeCommunities.every((c) => Number(c.dailyLimit || 0) > 0 && Number(c.actionsToday || 0) >= Number(c.dailyLimit || 0))
    ) {
      return "Community caps reached";
    }

    return null;
  })();
  const configuredRestRounds = Math.max(1, Number(settings?.roundsBeforeConnectionRest ?? connectionRest?.roundsBefore ?? 5));
  const configuredRestMinutes = Math.max(1, Number(settings?.connectionRestMinutes ?? connectionRest?.restMinutes ?? 5));

  const formatCountdown = (seconds: number) => {
    const safe = Math.max(0, seconds || 0);
    const m = Math.floor(safe / 60);
    const sec = safe % 60;
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };

  const secondsUntilNextMidnight = (() => {
    try {
      const now = new Date(nowMs);
      const parts = new Intl.DateTimeFormat("en-GB", {
        timeZone: UK_TIMEZONE,
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      }).formatToParts(now);
      const getPart = (type: string) => Number(parts.find((p) => p.type === type)?.value || "0");
      const h = getPart("hour");
      const m = getPart("minute");
      const s = getPart("second");
      const elapsed = (h * 3600) + (m * 60) + s;
      // Backend reset writes counters right after midnight (00:00:05).
      return Math.max(0, (24 * 3600) - elapsed + 5);
    } catch {
      return 0;
    }
  })();

  const nextCountdown = (() => {
    if (!engineStatus?.isRunning || engineStatus?.isPaused) return "Waiting for start";
    if (connectionRest?.active) return formatCountdown(connectionRest.remainingSeconds);
    if (isWaitingSchedule) {
      const sec = Math.max(0, Number(engineStatus?.countdownSeconds ?? 0));
      if (sec > 0) return formatCountdown(sec);
      return "Starting...";
    }
    if (displayActiveTask) return "Executing";
    if (!nextQueueItem) return emptyQueueReason || "--:--";
    const scheduledMs = parseQueueTimestampMs(
      String(nextQueueItem.scheduledFor || ""),
      String(nextQueueItem.scheduledTime || ""),
      adjustedQueueNowMs,
    );
    if (Number.isFinite(scheduledMs)) {
      const secondsLeft = Math.floor((scheduledMs - adjustedQueueNowMs) / 1000);
      if (secondsLeft <= 0) {
        return "Starting...";
      }
      return formatCountdown(secondsLeft);
    }
    if ((engineStatus.countdownSeconds ?? 0) <= 0) return "Starting...";
    return formatCountdown(engineStatus.countdownSeconds);
  })();
  useEffect(() => {
    const timer = window.setInterval(() => {
      setNowMs(Date.now());
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const isRunning = Boolean(engineStatus?.isRunning) && !Boolean(engineStatus?.isPaused);
    if (!isRunning) {
      if (queueTimerPauseOffsetMs !== 0) setQueueTimerPauseOffsetMs(0);
      restTrackingRef.current = { active: false, remainingSeconds: 0 };
      return;
    }

    const restActive = Boolean(connectionRest?.active);
    const remainingSeconds = restActive ? Math.max(0, Number(connectionRest?.remainingSeconds || 0)) : 0;
    const prev = restTrackingRef.current;
    if (restActive && prev.active) {
      const delta = Math.max(0, prev.remainingSeconds - remainingSeconds);
      if (delta > 0) {
        setQueueTimerPauseOffsetMs((value) => value + delta * 1000);
      }
    }
    restTrackingRef.current = { active: restActive, remainingSeconds };
  }, [connectionRest?.active, connectionRest?.remainingSeconds, engineStatus?.isRunning, engineStatus?.isPaused, queueTimerPauseOffsetMs]);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const response = await api.getOpenAIKey();
        if (active) {
          setOpenaiKey(response.apiKey ?? "");
        }
      } catch {
        return;
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  const filteredActivity = activityFeed.filter((item) => {
    if (activityFilterProfile && item.profile !== activityFilterProfile) return false;
    return true;
  });
  const displayedActivity = activityExpanded ? filteredActivity : filteredActivity.slice(0, 6);

  const handleExportCSV = () => {
    const csv = "Profile,Group,Action,Timestamp,Post URL\n" +
      filteredActivity.map((a) => `"${a.profile}","${a.groupName}","${a.action}","${a.timestamp}","${a.postUrl}"`).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "activity-export.csv";
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleSaveKey = async () => {
    if (!openaiKey.trim()) {
      toast.error("OpenAI API key is required");
      return;
    }
    if (isMaskedOpenAiKey(openaiKey.trim())) {
      toast.success("OpenAI API key is already saved");
      return;
    }
    await api.updateOpenAIKey({ apiKey: openaiKey.trim() });
    toast.success("OpenAI API key saved");
  };

  const handleTestKey = async () => {
    if (!openaiKey.trim()) return;
    setKeyTestStatus("testing");
    try {
      const candidate = openaiKey.trim();
      await api.automationTestOpenAI({ apiKey: isMaskedOpenAiKey(candidate) ? "" : candidate, prompt: "Reply with ok" });
      setKeyTestStatus("success");
      toast.success("OpenAI connection successful");
    } catch (error) {
      setKeyTestStatus("error");
      const message = error instanceof ApiError ? error.message : "OpenAI test failed";
      toast.error(message);
    }
    setTimeout(() => setKeyTestStatus("idle"), 3000);
  };

  const displayedQueue = queueExpanded ? displayQueue : displayQueue.slice(0, 6);

  const changeQueueRoundLimit = async (delta: number) => {
    if (!settings || queueRoundSavePending) return;
    const current = Number(settings.queuePrefillMaxPerProfilePerPass || 2);
    const next = Math.max(1, current + delta);
    if (next === current) return;
    setQueueRoundSavePending(true);
    try {
      await api.updateAutomationSettings({
        ...settings,
        queuePrefillMaxPerProfilePerPass: next,
      });
      await automationSettingsQuery.refetch();
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "Failed to update queue round limit";
      toast.error(message);
    } finally {
      setQueueRoundSavePending(false);
    }
  };

  return (
    <div className="p-6 lg:p-8 pt-16 md:pt-6 max-w-7xl">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-foreground">Dashboard</h1>
        <p className="text-sm text-muted-foreground mt-1">Overview of your engagement automation</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard icon={Users} label="Active Profiles" value={activeProfiles} sub={`${profiles.length} total В· Cap: ${settings?.globalDailyCapPerAccount ?? "-"}/day`} color="bg-primary/10 text-primary" />
        <StatCard icon={MessageSquare} label="Messages" value={messagesCount} sub={`${conversations.length} conversations`} color="bg-success/10 text-success" />
        <StatCard icon={Sparkles} label="Keyword Matches" value={keywordMatches} sub={`${visibleQueue.length} queued actions`} color="bg-warning/10 text-warning" />
        <div className="bg-card border border-border rounded-xl p-5 animate-count-up">
          <div className="flex items-center gap-3 mb-3">
            <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-info/10 text-info">
              <Clock className="w-5 h-5" />
            </div>
            <span className="text-sm font-medium text-muted-foreground">Next Action</span>
          </div>
          <p className="text-2xl font-bold text-foreground">{nextCountdown}</p>
          {isEngineRunning && connectionRest?.active ? (
            <>
              <p className="text-xs text-muted-foreground mt-1">
                Connection rest: pausing for {connectionRest.restMinutes} min
              </p>
              <p className="text-[11px] text-muted-foreground mt-1">
                Completed rounds: {Math.min(connectionRest.roundsCompleted, connectionRest.roundsBefore)} of {connectionRest.roundsBefore}
              </p>
              <div className="mt-2 flex items-center gap-2">
                <button onClick={() => setShowNextActions(true)} className="text-xs text-primary hover:text-primary/80 font-medium transition-colors inline-flex items-center gap-1">
                  View Next Scheduled &gt;
                </button>
              </div>
            </>
          ) : null}
          {!connectionRest?.active && isWaitingSchedule ? (
            <>
              <p className="text-xs text-muted-foreground mt-1">
                Awaiting next schedule window ({formatCountdown(Math.max(0, Number(engineStatus?.countdownSeconds || 0)))})
              </p>
              <p className="text-[11px] text-muted-foreground mt-1">
                Daily counters reset in {formatCountdown(secondsUntilNextMidnight)}
              </p>
              <div className="mt-2 flex items-center gap-2">
                <button onClick={() => setShowNextActions(true)} className="text-xs text-primary hover:text-primary/80 font-medium transition-colors inline-flex items-center gap-1">
                  View Next Scheduled &gt;
                </button>
              </div>
            </>
          ) : !connectionRest?.active && isEngineRunning && displayActiveTask ? (
            <>
              <p className="text-xs text-muted-foreground mt-1">
                {displayActiveTask.profile} В· <span className="text-primary font-medium">{displayActiveTask.stage}</span>
              </p>
              <div className="mt-2 flex items-center gap-2">
                <button onClick={() => setShowNextActions(true)} className="text-xs text-primary hover:text-primary/80 font-medium transition-colors inline-flex items-center gap-1">
                  View Next Scheduled &gt;
                </button>
              </div>
            </>
          ) : !connectionRest?.active && isEngineRunning && nextQueueItem ? (
            <>
              <p className="text-xs text-muted-foreground mt-1">
                {nextQueueItem.profile} &gt; {nextQueueItem.community} В· <span className="text-primary font-medium">{nextQueueItem.keyword}</span>
              </p>
              {recentRequeueTask && (
                <p className="text-[11px] text-warning mt-1">
                  Retrying task {recentRequeueTask.taskId.slice(0, 8)}... reason: {recentRequeueTask.reason.replaceAll("_", " ")}
                </p>
              )}
              <div className="mt-2 flex items-center gap-2">
                <button onClick={() => setShowNextActions(true)} className="text-xs text-primary hover:text-primary/80 font-medium transition-colors inline-flex items-center gap-1">
                  View Next Scheduled &gt;
                </button>
              </div>
            </>
          ) : (
            <p className="text-xs text-muted-foreground mt-1">
              {engineStatus?.isPaused
                ? "Automation paused"
                : engineStatus?.isRunning
                  ? (connectionRest?.active
                    ? "Connection rest is active"
                    : recentRequeueTask
                      ? `Retry pending: ${recentRequeueTask.reason.replaceAll("_", " ")}`
                      : "No scheduled actions")
                  : "Automation stopped"}
            </p>
          )}
          <p className="text-[11px] text-muted-foreground mt-2">
            Rest policy: every {configuredRestRounds} rounds, pause for {configuredRestMinutes} min.
          </p>
        </div>
      </div>

      <div className="bg-card border border-border rounded-xl p-5 mb-6">
        <div className="flex items-center gap-2 mb-3">
          <Key className="w-4 h-4 text-muted-foreground" />
          <h3 className="text-sm font-semibold text-foreground">OpenAI API Key</h3>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="password"
            value={openaiKey}
            onChange={(e) => setOpenaiKey(e.target.value)}
            placeholder="sk-..."
            className="flex-1 px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground font-mono focus:outline-none focus:ring-2 focus:ring-ring"
          />
          <button onClick={handleSaveKey} className="px-3 py-2 rounded-lg border border-border text-sm font-medium text-foreground hover:bg-muted transition-colors">
            Save
          </button>
          <button
            onClick={handleTestKey}
            disabled={keyTestStatus === "testing" || !openaiKey.trim()}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
          >
            {keyTestStatus === "testing" ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> :
              keyTestStatus === "success" ? <CheckCircle className="w-3.5 h-3.5" /> :
                keyTestStatus === "error" ? <XCircle className="w-3.5 h-3.5" /> : null}
            {keyTestStatus === "testing" ? "Testing..." : keyTestStatus === "success" ? "Connected!" : keyTestStatus === "error" ? "Failed" : "Test Key"}
          </button>
        </div>
        <p className="text-[11px] text-muted-foreground mt-2">Used for AI-powered comment and DM generation</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-card border border-border rounded-xl">
          <div className="flex items-center justify-between px-5 py-4 border-b border-border">
            <div className="flex items-center gap-2">
              <Activity className="w-4 h-4 text-muted-foreground" />
              <h2 className="text-sm font-semibold text-foreground">Activity Timeline</h2>
            </div>
            <div className="flex items-center gap-2">
              <select
                value={activityFilterProfile}
                onChange={(e) => setActivityFilterProfile(e.target.value)}
                className="text-xs px-2 py-1 rounded-md border border-border bg-background text-foreground"
              >
                <option value="">All Profiles</option>
                {profiles.map((p) => <option key={p.id} value={p.name}>{p.name}</option>)}
              </select>
              <button onClick={handleExportCSV} className="p-1.5 rounded-md hover:bg-muted transition-colors" title="Export CSV">
                <Download className="w-3.5 h-3.5 text-muted-foreground" />
              </button>
            </div>
          </div>
          <div className="divide-y divide-border">
            {displayedActivity.map((item) => (
              <div key={item.id} className="flex items-start gap-3 px-5 py-3.5 animate-slide-in">
                <div className="flex items-center justify-center w-8 h-8 rounded-full bg-primary/10 text-primary text-xs font-semibold shrink-0 mt-0.5">
                  {item.profile.split(" ").map((n) => n[0]).join("")}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-foreground">
                    <span className="font-medium">{item.profile}</span>
                    <span className="text-muted-foreground"> В· {prettifyGroupName(item.groupName, item.postUrl)}</span>
                  </p>
                  <p className="text-sm text-muted-foreground truncate">{item.action}</p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-xs text-muted-foreground whitespace-nowrap">{formatRelativeTime(item.timestamp)}</span>
                  <a href={item.postUrl} className="text-primary hover:text-primary/80"><ExternalLink className="w-3 h-3" /></a>
                </div>
              </div>
            ))}
            {displayedActivity.length === 0 && (
              <div className="px-5 py-8 text-sm text-muted-foreground">No activity yet.</div>
            )}
          </div>
          {!activityExpanded && filteredActivity.length > 6 && (
            <button
              onClick={() => setActivityExpanded(true)}
              className="w-full py-2.5 text-xs text-primary hover:bg-muted/50 transition-colors border-t border-border"
            >
              Show all {filteredActivity.length} activity rows
            </button>
          )}
          {activityExpanded && filteredActivity.length > 6 && (
            <button
              onClick={() => setActivityExpanded(false)}
              className="w-full py-2.5 text-xs text-muted-foreground hover:bg-muted/50 transition-colors border-t border-border"
            >
              Show less
            </button>
          )}
        </div>

        <div className="bg-card border border-border rounded-xl">
          <div className="flex items-center justify-between px-5 py-4 border-b border-border">
            <div className="flex items-center gap-2">
              <Clock className="w-4 h-4 text-muted-foreground" />
              <h2 className="text-sm font-semibold text-foreground">Action Queue</h2>
              <span className="text-xs text-muted-foreground">({queue.length > 0 ? `${visibleQueue.length} scheduled` : visibleQueue.length > 0 ? `0 queued + ${visibleQueue.length} forecast` : "0 scheduled"})</span>
              <div className="ml-2 inline-flex items-center rounded-md border border-border bg-background overflow-hidden">
                <button
                  type="button"
                  onClick={() => void changeQueueRoundLimit(-1)}
                  disabled={!settings || queueRoundSavePending}
                  className="px-2 py-1 text-xs font-semibold text-muted-foreground hover:bg-muted disabled:opacity-50"
                  aria-label="Decrease tasks per profile per round"
                >
                  -
                </button>
                <span className="min-w-8 px-2 py-1 text-center text-xs font-medium text-foreground border-x border-border">
                  {settings?.queuePrefillMaxPerProfilePerPass ?? 2}
                </span>
                <button
                  type="button"
                  onClick={() => void changeQueueRoundLimit(1)}
                  disabled={!settings || queueRoundSavePending}
                  className="px-2 py-1 text-xs font-semibold text-muted-foreground hover:bg-muted disabled:opacity-50"
                  aria-label="Increase tasks per profile per round"
                >
                  +
                </button>
              </div>
            </div>
            {visibleQueue.length > 6 && (
              <button onClick={() => setQueueExpanded(!queueExpanded)} className="p-1.5 rounded-md hover:bg-muted transition-colors" title={queueExpanded ? "Collapse" : "Expand all"}>
                {queueExpanded ? <ChevronUp className="w-4 h-4 text-muted-foreground" /> : <ChevronDown className="w-4 h-4 text-muted-foreground" />}
              </button>
            )}
          </div>
          <div className="divide-y divide-border">
            {displayedQueue.map((item) => (
              <div key={item.id} className={`px-5 py-3.5 ${queueExpanded ? "flex items-start" : "flex items-center gap-4"}`}>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-foreground">{item.profile}</p>
                  <p className="text-xs text-muted-foreground">{item.community} В· <span className="text-primary">{item.keyword}</span></p>
                  {queueExpanded && (
                    <p className="text-[11px] text-muted-foreground mt-0.5">Post: {item.postId} В· Priority: {item.priorityScore}</p>
                  )}
                </div>
                {!queueExpanded && (
                  <div className="text-right">
                    <p className="text-sm font-mono font-semibold text-foreground">
                      {formatQueueEta(
                        item.scheduledFor,
                        item.scheduledTime,
                        adjustedQueueNowMs,
                        Boolean(engineStatus?.isRunning && !engineStatus?.isPaused),
                        Boolean(
                          activeTask ||
                          (queueExecutionWindowActive && nextQueueItem && String(item.id) !== String(nextQueueItem.id))
                        ),
                        connectionRest?.active ? Math.max(0, Number(connectionRest.remainingSeconds || 0)) : 0,
                      )}
                    </p>
                    <p className="text-[10px] text-muted-foreground">
                      {engineStatus?.isRunning && !engineStatus?.isPaused ? item.scheduledTime : "Waiting for start"}
                    </p>
                  </div>
                )}
              </div>
            ))}
            {displayedQueue.length === 0 && (
              <div className="px-5 py-8 text-sm text-muted-foreground">Queue is empty.</div>
            )}
          </div>
          {!queueExpanded && visibleQueue.length > 6 && (
            <button onClick={() => setQueueExpanded(true)} className="w-full py-2.5 text-xs text-primary hover:bg-muted/50 transition-colors border-t border-border">
              {`Show all ${visibleQueue.length} scheduled actions`}
            </button>
          )}
          {queueExpanded && visibleQueue.length > 6 && (
            <button
              onClick={() => setQueueExpanded(false)}
              className="w-full py-2.5 text-xs text-muted-foreground hover:bg-muted/50 transition-colors border-t border-border"
            >
              Show less
            </button>
          )}
        </div>
      </div>

      <NextActionsDrawer open={showNextActions} onClose={() => setShowNextActions(false)} />
    </div>
  );
}






