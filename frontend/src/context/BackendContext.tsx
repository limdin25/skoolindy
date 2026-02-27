import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/api";
import type { AutomationSettings, Conversation, KeywordRule, Label, LogEntry, Profile, Community } from "@/lib/types";
import { getBackendBaseUrl } from "@/lib/api";
import type { AutomationEngineStatus } from "@/lib/api";

interface BackendContextValue {
  profiles: Profile[];
  communities: Community[];
  conversations: Conversation[];
  labels: Label[];
  keywordRules: KeywordRule[];
  automationSettings: AutomationSettings | null;
  logs: LogEntry[];
  engineStatus: AutomationEngineStatus | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  refreshLogs: () => Promise<void>;
}

const BackendContext = createContext<BackendContextValue | undefined>(undefined);

type SettledResult<T> = PromiseSettledResult<T>;

function getSettledValue<T>(result: SettledResult<T>): T | null {
  return result.status === "fulfilled" ? result.value : null;
}

function getSettledError(result: SettledResult<unknown>): unknown | null {
  return result.status === "rejected" ? result.reason : null;
}

export function BackendProvider({ children }: { children: React.ReactNode }) {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [communities, setCommunities] = useState<Community[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [labels, setLabels] = useState<Label[]>([]);
  const [keywordRules, setKeywordRules] = useState<KeywordRule[]>([]);
  const [automationSettings, setAutomationSettings] = useState<AutomationSettings | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [engineStatus, setEngineStatus] = useState<AutomationEngineStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const hasLoadedRef = useRef(false);

  const refreshLogs = useCallback(async () => {
    setLogs(await api.getLogs());
  }, []);

  const refresh = useCallback(async () => {
    if (!hasLoadedRef.current) {
      setLoading(true);
    }
    setError(null);
    const loadingGuard = globalThis.setTimeout(() => setLoading(false), 15000);
    try {
      const [profilesRes, communitiesRes, conversationsRes, labelsRes, keywordRulesRes, automationSettingsRes, logsRes, statusRes] =
        await Promise.allSettled([
          api.getProfiles(),
          api.getCommunities(),
          api.getConversations(false),
          api.getLabels(),
          api.getKeywordRules(),
          api.getAutomationSettings(),
          api.getLogs(),
          api.automationStatus(),
        ]);

      const profilesData = getSettledValue(profilesRes);
      const communitiesData = getSettledValue(communitiesRes);
      const conversationsData = getSettledValue(conversationsRes);
      const labelsData = getSettledValue(labelsRes);
      const keywordRulesData = getSettledValue(keywordRulesRes);
      const settingsData = getSettledValue(automationSettingsRes);
      const logsData = getSettledValue(logsRes);
      const statusData = getSettledValue(statusRes);

      if (profilesData !== null) setProfiles(profilesData);
      if (communitiesData !== null) setCommunities(communitiesData);
      if (conversationsData !== null) setConversations(conversationsData);
      if (labelsData !== null) setLabels(labelsData);
      if (keywordRulesData !== null) setKeywordRules(keywordRulesData);
      if (settingsData !== null) setAutomationSettings(settingsData);
      if (logsData !== null) setLogs(logsData);
      if (statusData !== null) setEngineStatus(statusData);

      const errors = [profilesRes, communitiesRes, conversationsRes, labelsRes, keywordRulesRes, automationSettingsRes, logsRes, statusRes]
        .map((item) => getSettledError(item))
        .filter((item) => item !== null);
      if (errors.length > 0) {
        const first = errors[0];
        setError(first instanceof Error ? first.message : "Some backend requests failed");
      }
      hasLoadedRef.current = true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load backend data");
    } finally {
      globalThis.clearTimeout(loadingGuard);
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const id = window.setInterval(() => {
      api.automationStatus()
        .then((status) => setEngineStatus(status))
        .catch(() => setEngineStatus(null));
    }, 2000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    const id = window.setInterval(() => {
      const isInboxRoute = window.location.pathname.toLowerCase().includes("inbox");
      if (!isInboxRoute) return;
      api.getConversations(false)
        .then((data) => setConversations(data))
        .catch(() => undefined);
    }, 60000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    let source: EventSource | null = null;
    let closed = false;
    let reconnectTimer: number | null = null;

    const connect = async () => {
      try {
        const baseUrl = await getBackendBaseUrl();
        if (closed) return;
        source = new EventSource(`${baseUrl}/automation/logs/stream`);
        source.onmessage = (event) => {
          try {
            const payload = JSON.parse(event.data);
            if (payload.type === "heartbeat") {
              return;
            }
            setLogs((prev) => [payload, ...prev].slice(0, 500));
          } catch {
            return;
          }
        };
        source.onerror = () => {
          source?.close();
          source = null;
          if (closed) return;
          if (reconnectTimer !== null) {
            window.clearTimeout(reconnectTimer);
          }
          reconnectTimer = window.setTimeout(() => {
            reconnectTimer = null;
            void connect();
          }, 2500);
        };
      } catch {
        if (closed) return;
        if (reconnectTimer !== null) {
          window.clearTimeout(reconnectTimer);
        }
        reconnectTimer = window.setTimeout(() => {
          reconnectTimer = null;
          void connect();
        }, 2500);
      }
    };

    void connect();

    return () => {
      closed = true;
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
      }
      source?.close();
    };
  }, []);

  useEffect(() => {
    const id = window.setInterval(() => {
      api.getLogs()
        .then((data) => setLogs(data))
        .catch(() => undefined);
    }, 12000);
    return () => window.clearInterval(id);
  }, []);

  const value = useMemo(
    () => ({
      profiles,
      communities,
      conversations,
      labels,
      keywordRules,
      automationSettings,
      logs,
      engineStatus,
      loading,
      error,
      refresh,
      refreshLogs,
    }),
    [profiles, communities, conversations, labels, keywordRules, automationSettings, logs, engineStatus, loading, error, refresh, refreshLogs],
  );

  return <BackendContext.Provider value={value}>{children}</BackendContext.Provider>;
}

export function useBackend() {
  const context = useContext(BackendContext);
  if (!context) {
    throw new Error("useBackend must be used within BackendProvider");
  }
  return context;
}
