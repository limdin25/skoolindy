import type {
  AnalyticsData,
  ActivityEntry,
  AutomationSettings,
  Community,
  Conversation,
  KeywordRule,
  Label,
  LogEntry,
  Profile,
  QueueItem,
  QueueListResponse,
  QueuePreviewItem,
} from "./types";

const normalizeBase = (value: string) => value.replace(/\/+$/, "");
const ENV_BASE_URL = (import.meta.env.VITE_BACKEND_URL as string | undefined)?.trim();
let cachedBaseUrl: string | null = ENV_BASE_URL ? normalizeBase(ENV_BASE_URL) : null;
let resolveBaseInFlight: Promise<string> | null = null;
const REQUEST_TIMEOUT_MS = 15000;
const JSON_CONTENT_TYPE = "application/json";

function timeoutForPath(path: string): number {
  const p = (path || "").toLowerCase();
  // Community sync can take longer because Playwright iterates profiles one by one.
  if (p.includes("/communities/fetch")) return 90000;
  if (p.includes("/check-login")) return 70000;
  if (p.includes("/connect-skool")) return 70000;
  if (p.includes("/check-proxy")) return 70000;
  if (p.includes("/conversations") && p.includes("sync=true")) return 70000;
  if (p.includes("/conversations/") && p.includes("/messages")) return 45000;
  if (p.includes("/n8n-scan-now")) return 300000; // Scans can take minutes (Playwright)
  return REQUEST_TIMEOUT_MS;
}

async function fetchWithTimeout(url: string, init: RequestInit = {}, timeoutMs: number): Promise<Response> {
  if (init.signal) {
    return fetch(url, init);
  }
  const controller = new AbortController();
  const timer = globalThis.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    globalThis.clearTimeout(timer);
  }
}

const getFallbackBaseCandidates = () => {
  if (typeof window === "undefined") {
    return ["http://localhost:8000", "http://127.0.0.1:8000"];
  }

  const protocol = window.location.protocol.toLowerCase();
  const origin = window.location.origin;
  const hostname = window.location.hostname;
  const isHttpOrigin = protocol === "http:" || protocol === "https:";
  const isLocalHost = /^(localhost|127\.0\.0\.1)$/i.test(hostname);

  const candidates: string[] = [];
  if (isHttpOrigin) {
    // Prefer same-host API gateway first (Docker/Nginx), then plain origin fallback.
    candidates.push(`${origin}/api`, origin);
  }

  if (hostname && !isLocalHost) {
    candidates.push(`http://${hostname}:8000`, `http://${hostname}:8000/api`);
  }

  candidates.push("http://localhost:8000", "http://127.0.0.1:8000");
  return [...new Set(candidates)];
};

async function resolveBackendBaseUrl(path: string): Promise<string> {
  if (cachedBaseUrl) {
    return cachedBaseUrl;
  }
  if (resolveBaseInFlight) {
    return resolveBaseInFlight;
  }

  resolveBaseInFlight = (async () => {
    for (const candidate of getFallbackBaseCandidates()) {
      const normalized = normalizeBase(candidate);
      try {
        const response = await fetchWithTimeout(`${normalized}${path}`, {
          method: "GET",
          cache: "no-cache",
          headers: { Accept: "application/json" },
        }, timeoutForPath(path));

        if (response.status === 404 || response.status === 405) {
          continue;
        }

        const text = await response.text();
        if (!text) {
          continue;
        }
        const parsed = JSON.parse(text) as unknown;
        if (parsed && typeof parsed === "object") {
          cachedBaseUrl = normalized;
          return normalized;
        }
      } catch {
        continue;
      }
    }
    cachedBaseUrl = "http://localhost:8000";
    return cachedBaseUrl;
  })();
  try {
    return await resolveBaseInFlight;
  } finally {
    resolveBaseInFlight = null;
  }
}

export class ApiError extends Error {
  status: number;
  error: string;

  constructor(status: number, error: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.error = error;
  }
}

type ErrorPayload = {
  success?: boolean;
  error?: string;
  message?: string;
};

function isLikelyJsonResponse(response: Response): boolean {
  if (response.status === 204) {
    return true;
  }
  const contentType = (response.headers.get("content-type") || "").toLowerCase();
  if (!contentType) {
    return true;
  }
  return contentType.includes(JSON_CONTENT_TYPE);
}

function parseJsonSafe(text: string): unknown | null {
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const primaryBase = await resolveBackendBaseUrl(path);
  const baseCandidates = [primaryBase, ...getFallbackBaseCandidates().map(normalizeBase).filter((item) => item !== primaryBase)];
  let response: Response | null = null;

  for (const base of baseCandidates) {
    try {
      const current = await fetchWithTimeout(`${base}${path}`, {
        cache: "no-cache",
        headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
        ...init,
      }, timeoutForPath(path));

      if (current.status === 404 || current.status === 405) {
        continue;
      }

      if (!isLikelyJsonResponse(current)) {
        continue;
      }

      response = current;
      cachedBaseUrl = base;
      break;
    } catch {
      continue;
    }
  }

  if (!response) {
    throw new ApiError(0, "network_error", "Backend is unreachable");
  }

  const text = await response.text();
  const parsed = parseJsonSafe(text);

  const payload = (parsed && typeof parsed === "object" ? parsed : null) as ErrorPayload | null;
  const message =
    payload?.message ||
    (text && !payload ? text : "") ||
    `${response.status} ${response.statusText}`;
  const machineError = payload?.error || "request_error";

  if (!response.ok) {
    throw new ApiError(response.status, machineError, message);
  }
  if (response.status === 204) {
    return undefined as T;
  }

  if (payload && payload.success === false) {
    throw new ApiError(response.status, machineError, message);
  }

  if (parsed === null) {
    throw new ApiError(response.status, "invalid_response", "Invalid server response");
  }

  return parsed as T;
}

export interface AutomationEngineStatus {
  success: boolean;
  isRunning: boolean;
  isPaused: boolean;
  state: string;
  runState: string;
  countdownSeconds: number;
  connectionRest?: {
    active: boolean;
    remainingSeconds: number;
    roundsBefore: number;
    roundsCompleted: number;
    restMinutes: number;
  };
  currentProfileIndex: number;
  profiles: Array<Record<string, unknown>>;
  stats: Record<string, number>;
  activity: Array<Record<string, unknown>>;
}

export interface CommunityFetchProfileResult {
  profileId: string;
  profileName: string;
  discovered: number;
  created: number;
  updated: number;
  skipped: number;
  total?: number | null;
  joined?: number | null;
  pendingExcluded?: number | null;
  unknownExcluded?: number | null;
  error?: string | null;
}

export interface CommunityFetchResponse {
  success: boolean;
  profilesProcessed: number;
  discovered: number;
  created: number;
  updated: number;
  skipped: number;
  totalFetched?: number | null;
  totalJoined?: number | null;
  totalPendingExcluded?: number | null;
  totalUnknownExcluded?: number | null;
  results: CommunityFetchProfileResult[];
}

export interface CommunityFetchStatus {
  running: boolean;
  startedAt: string;
  finishedAt: string;
  profilesTotal: number;
  profilesDone: number;
  currentProfileId: string;
  currentProfileName: string;
  lastError: string;
  lastResult?: CommunityFetchResponse | null;
}

export const getBackendBaseUrl = async () => resolveBackendBaseUrl("/automation/status");

export interface AdminSummaryResponse {
  profiles: Array<{ email: string; actionsToday: number; dailyCap: number; status: string; lastActionAt: string | null; failuresToday: number }>;
  communities: Array<{ name: string; profile: string; actionsToday: number; dailyCap: number; editorFailures: number; skippedToday: boolean; lastFailureReason: string | null }>;
  queue: { pending: number; retrying: number; skipped: number; failedToday: number };
  skippedPosts: Array<{ url: string; profile: string; reason: string; skippedAt: string }>;
}

export const api = {
  getProfiles: () => request<Profile[]>("/profiles"),
  createProfile: (payload: Omit<Profile, "id">) => request<Profile>("/profiles", { method: "POST", body: JSON.stringify(payload) }),
  updateProfile: (id: string, payload: Partial<Profile>) => request<Profile>(`/profiles/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  deleteProfile: (id: string) => request<{ success: boolean }>(`/profiles/${id}`, { method: "DELETE" }),
  profileResetCounters: (profileId: string) => request<{ success: boolean }>(`/profiles/${profileId}/reset-counters`, { method: "POST" }),
  connectSkool: (payload: { email: string; password: string }) =>
    request<{ success: boolean; profileId?: string; message?: string }>("/connect-skool", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getCommunities: () => request<Community[]>("/communities"),
  fetchCommunities: () => request<CommunityFetchStatus>("/communities/fetch", { method: "POST" }),
  getCommunitiesFetchStatus: () => request<CommunityFetchStatus>(`/communities/fetch-status?t=${Date.now()}`),
  createCommunity: (payload: Omit<Community, "id">) => request<Community>("/communities", { method: "POST", body: JSON.stringify(payload) }),
  updateCommunity: (id: string, payload: Partial<Community>) => request<Community>(`/communities/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  deleteCommunity: (id: string) => request<{ success: boolean }>(`/communities/${id}`, { method: "DELETE" }),

  getLabels: () => request<Label[]>("/labels"),
  createLabel: (payload: Omit<Label, "id">) => request<Label>("/labels", { method: "POST", body: JSON.stringify(payload) }),
  updateLabel: (id: string, payload: Partial<Label>) => request<Label>(`/labels/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  deleteLabel: (id: string) => request<{ success: boolean }>(`/labels/${id}`, { method: "DELETE" }),

  getKeywordRules: () => request<KeywordRule[]>("/keyword-rules"),
  createKeywordRule: (payload: Omit<KeywordRule, "id">) => request<KeywordRule>("/keyword-rules", { method: "POST", body: JSON.stringify(payload) }),
  updateKeywordRule: (id: string, payload: Partial<KeywordRule>) => request<KeywordRule>(`/keyword-rules/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  deleteKeywordRule: (id: string) => request<{ success: boolean }>(`/keyword-rules/${id}`, { method: "DELETE" }),

  getAutomationSettings: () => request<AutomationSettings>("/automation/settings"),
  updateAutomationSettings: (payload: AutomationSettings) => request<AutomationSettings>("/automation/settings", { method: "PUT", body: JSON.stringify(payload) }),

  getQueue: () => request<QueueListResponse>("/queue"),
  getQueuePreview: (limit = 50, days = 2) => request<QueuePreviewItem[]>(`/queue/preview?limit=${limit}&days=${days}`),
  updateQueueItem: (id: string, payload: Partial<QueueItem>) => request<QueueItem>(`/queue/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  queueStartSoon: (id: string, seconds = 10) => request<QueueItem>(`/queue/${id}/start-soon?seconds=${Math.max(1, Math.floor(seconds))}`, { method: "POST" }),
  deleteQueueItem: (id: string) => request<{ success: boolean }>(`/queue/${id}`, { method: "DELETE" }),

  getLogs: (limit = 500) => request<LogEntry[]>(`/logs?limit=${Math.max(50, Math.min(2000, Math.floor(limit || 500)))}`),
  clearLogs: () => request<{ success: boolean; deleted: number }>("/logs", { method: "DELETE" }),
  getActivity: () => request<ActivityEntry[]>("/activity"),
  getAnalytics: () => request<AnalyticsData>("/analytics"),

  getConversations: (sync = false) => request<Conversation[]>(`/conversations${sync ? "?sync=true" : ""}`),
  patchConversation: (id: string, payload: Partial<Pick<Conversation, "labelId" | "isArchived" | "isDeletedUi" | "unread" | "aiAutoEnabled">>) =>
    request<Conversation>(`/conversations/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteConversation: (id: string) => request<{ success: boolean }>(`/conversations/${id}`, { method: "DELETE" }),
  addMessage: (conversationId: string, payload: { text: string; sender: "outbound" | "inbound"; timestamp?: string }) =>
    request<Conversation>(`/conversations/${conversationId}/messages`, { method: "POST", body: JSON.stringify(payload) }),
  aiSuggestMessage: (conversationId: string, payload: { tone: "Friendly" | "Authority" | "Consultant" | "Casual" }) =>
    request<{ success: boolean; text: string; source: string; model?: string | null }>(
      `/conversations/${conversationId}/ai-suggest`,
      { method: "POST", body: JSON.stringify(payload) },
    ),

  automationStart: (payload: { profiles?: Array<Record<string, unknown>>; globalSettings?: Record<string, unknown> } = {}) =>
    request<AutomationEngineStatus>("/automation/start", { method: "POST", body: JSON.stringify(payload) }),
  automationStop: () => request<AutomationEngineStatus>("/automation/stop", { method: "POST" }),
  automationPause: () => request<AutomationEngineStatus>("/automation/pause", { method: "POST" }),
  automationResume: () => request<AutomationEngineStatus>("/automation/resume", { method: "POST" }),
  automationResetTasks: () => request<{ success: boolean; queueDeleted: number; activityDeleted: number }>("/automation/reset-tasks", { method: "POST" }),
  automationStatus: () => request<AutomationEngineStatus>("/automation/status"),

  n8nScanNow: () => request<{ success: boolean; scanned: number; posts_found: number; queued: number; skipped: number; errors: string[] }>("/automation/n8n-scan-now", { method: "POST" }),
  n8nTiming: () => request<{ lastScanTime: string | null; lastExecuteTime: string | null; lastQueueInsert: string | null; pendingQueueItems: number; nextScheduledFor: string | null; masterEnabled: boolean; isN8nMode: boolean; executorIntervalSeconds: number }>("/automation/n8n-timing"),
  profileCheckLogin: (profileId: string) => request<{ success: boolean; status: string; message: string }>(`/profiles/${profileId}/check-login`, { method: "POST" }),
  profileCheckProxy: (profileId: string) => request<{ success: boolean; status: string; message: string }>(`/profiles/${profileId}/check-proxy`, { method: "POST" }),
  automationCheckLogin: (profileId: string) => request<{ success: boolean; status: string; message: string }>(`/automation/profiles/${profileId}/check-login`, { method: "POST" }),
  automationProofRun: (payload: { profileId: string; communityUrl: string }) => request<{ success: boolean; postUrl?: string }>(`/automation/proof-run`, { method: "POST", body: JSON.stringify(payload) }),
  automationTestComment: (payload: { profileId: string; communityUrl: string; prompt: string; apiKey?: string }) =>
    request<{ success: boolean; postUrl?: string; aiReply?: string }>(`/automation/test-comment`, { method: "POST", body: JSON.stringify(payload) }),
  automationTestOpenAI: (payload: { apiKey: string; prompt?: string }) =>
    request<{ success: boolean; message: string; response: string }>(`/automation/test-openai`, { method: "POST", body: JSON.stringify(payload) }),

  getOpenAIKey: () => request<{ success: boolean; apiKey: string; isConfigured: boolean }>(`/automation/openai-key`),
  updateOpenAIKey: (payload: { apiKey: string }) =>
    request<{ success: boolean; isConfigured: boolean }>(`/automation/openai-key`, { method: "PUT", body: JSON.stringify(payload) }),

  getAdminSummary: () => request<AdminSummaryResponse>("/admin/summary"),
  adminResetCommunityFailures: (communityId?: string) =>
    request<{ success: boolean }>("/admin/reset-community-failures", { method: "POST", body: JSON.stringify(communityId ? { communityId } : {}) }),
  adminUnskipPost: (postUrl: string) =>
    request<{ success: boolean }>("/admin/unskip-post", { method: "POST", body: JSON.stringify({ postUrl }) }),
  adminClearSkippedPosts: () => request<{ success: boolean }>("/admin/clear-skipped-posts", { method: "POST" }),
  adminResetDailyCounts: () => request<{ success: boolean }>("/admin/reset-daily-counts", { method: "POST" }),
};
