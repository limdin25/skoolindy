import { useEffect, useRef, useState } from "react";
import { useCommunities, useProfiles } from "@/hooks/useEngageFlow";
import { api } from "@/lib/api";
import type { CommunityFetchStatus } from "@/lib/api";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Plus, Globe, X, RefreshCw, LogIn, Pause, Play, Upload } from "lucide-react";
import { toast } from "sonner";

export default function CommunitiesPage() {
  const communitiesQuery = useCommunities();
  const profilesQuery = useProfiles();
  const communities = communitiesQuery.data ?? [];
  const profiles = profilesQuery.data ?? [];
  const [showAdd, setShowAdd] = useState(false);
  const [newName, setNewName] = useState("");
  const [newUrl, setNewUrl] = useState("");
  const [newProfile, setNewProfile] = useState("");
  const [newDailyLimit, setNewDailyLimit] = useState(5);
  const [newMaxPostAgeDays, setNewMaxPostAgeDays] = useState(0);
  const [fetchStatus, setFetchStatus] = useState<CommunityFetchStatus | null>(null);
  const wasFetchingRef = useRef(false);
  const lastHandledFinishedAtRef = useRef("");
  const lastRunningRefreshAtRef = useRef(0);
  const lastCompletionRefreshKeyRef = useRef("");
  const [viewProfile, setViewProfile] = useState("");
  const [loginInProgress, setLoginInProgress] = useState<string | null>(null);

  // Join tab mock state
  const [joinUrls, setJoinUrls] = useState("");
  const [joinProfileIds, setJoinProfileIds] = useState<string[]>([]);
  const [joinAllProfiles, setJoinAllProfiles] = useState(false);
  const [mockJob, setMockJob] = useState<{
    id: string;
    completed: number;
    total: number;
    rows: { profile: string; url: string; status: string; attempts: number; reason: string }[];
    paused: boolean;
  } | null>(null);

  useEffect(() => {
    if (!newProfile && profiles.length > 0) {
      setNewProfile(profiles[0].id);
    }
  }, [newProfile, profiles]);

  useEffect(() => {
    let disposed = false;
    const loadStatus = async () => {
      try {
        const status = await api.getCommunitiesFetchStatus();
        if (!disposed) {
          setFetchStatus(status);
        }
      } catch {
        return;
      }
    };
    void loadStatus();
    const timer = window.setInterval(() => {
      void loadStatus();
    }, 2000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    const running = !!fetchStatus?.running;
    const wasRunning = wasFetchingRef.current;
    wasFetchingRef.current = running;
    if (!fetchStatus) return;

    if (running) {
      const now = Date.now();
      if (now - lastRunningRefreshAtRef.current >= 6000) {
        lastRunningRefreshAtRef.current = now;
        void communitiesQuery.refetch();
      }
    }

    const finishedAt = String(fetchStatus.finishedAt || "").trim();
    const completionKey = finishedAt || `${fetchStatus.startedAt}|${fetchStatus.profilesDone}|${fetchStatus.lastError}`;
    if (wasRunning && !running && completionKey && lastCompletionRefreshKeyRef.current !== completionKey) {
      lastCompletionRefreshKeyRef.current = completionKey;
      void refreshAll();
    }
    if (!running && finishedAt && lastHandledFinishedAtRef.current !== finishedAt) {
      lastHandledFinishedAtRef.current = finishedAt;
      void refreshAll();
      if (fetchStatus.lastError) {
        toast.error(`Communities sync failed: ${fetchStatus.lastError}`);
        return;
      }
      const result = fetchStatus.lastResult;
      if (!result) {
        toast.warning("Communities sync finished, but no summary returned");
        return;
      }
      const isLoginRequired = (err: string | null | undefined) => {
        if (!err) return false;
        const low = err.toLowerCase();
        return (
          low.includes("no cookies") ||
          low.includes("not logged in") ||
          low.includes("cookie") ||
          low.includes("expired") ||
          low.includes("unauthorized") ||
          low.includes("401") ||
          low.includes("403")
        );
      };

      const loginRequired = result.results
        .filter((r) => r.error && isLoginRequired(r.error))
        .map((r) => r.profileName);

      const realFailed = result.results
        .filter((r) => r.error && !isLoginRequired(r.error))
        .map((r) => r.profileName);

      const loginNames = loginRequired.length ? loginRequired.join(", ") : "";
      const failedNames = realFailed.length ? realFailed.join(", ") : "";

      if (realFailed.length > 0) {
        const parts = ["Failed: " + failedNames];
        if (loginNames) parts.push("Login required: " + loginNames);
        toast.error("Communities synced with errors. " + parts.join(". ") + ".");
        return;
      }

      if (loginRequired.length > 0) {
        toast.warning("Communities synced. Login required: " + loginNames);
        return;
      }

      toast.success("Communities synced. Created: " + result.created + ", updated: " + result.updated + ".");
      return;
    }

    if (wasRunning && !running && !finishedAt) {
      void refreshAll();
    }
  }, [fetchStatus]);

  const grouped = profiles.map(p => ({
    profile: p,
    communities: communities.filter(c => c.profileId === p.id),
  })).filter(g => g.communities.length > 0 && (!viewProfile || g.profile.id === viewProfile));

  const refreshAll = async () => {
    await Promise.all([communitiesQuery.refetch(), profilesQuery.refetch()]);
  };

  const handleAdd = async () => {
    if (!newName.trim()) return;
    await api.createCommunity({
      profileId: newProfile,
      name: newName,
      url: newUrl,
      dailyLimit: Math.max(0, newDailyLimit),
      maxPostAgeDays: Math.max(0, newMaxPostAgeDays),
      lastScanned: "",
      status: 'active' as const,
      matchesToday: 0,
      actionsToday: 0,
      totalScannedPosts: 0,
      totalKeywordMatches: 0,
    });
    await refreshAll();
    setNewName("");
    setNewUrl("");
    setNewDailyLimit(5);
    setNewMaxPostAgeDays(0);
    setShowAdd(false);
  };

  const handleProfileLogin = async (profileId: string, profileName: string) => {
    if (loginInProgress) return;
    setLoginInProgress(profileId);
    try {
      const res = await api.profileCheckLogin(profileId);
      if (res?.success) {
        toast.success("Login saved for " + profileName);
        await refreshAll();
        void api.fetchCommunities().then(setFetchStatus);
      } else {
        toast.error(res?.message || "Login failed");
      }
    } catch (e) {
      toast.error((e as Error)?.message || "Login failed");
    } finally {
      setLoginInProgress(null);
    }
  };

  const handleFetch = async () => {
    try {
      const status = await api.fetchCommunities();
      setFetchStatus(status);
      if (status.running) {
        const total = Math.max(0, Number(status.profilesTotal || 0));
        if (total > 0) {
          toast.info(`Communities sync started: ${status.profilesDone}/${total} profiles`);
        } else {
          toast.info("Communities sync started");
        }
      } else if (status.lastError) {
        toast.error(`Communities sync failed: ${status.lastError}`);
        void refreshAll();
      } else {
        void refreshAll();
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to fetch communities";
      toast.error(message);
    }
  };

  const updateLimit = async (id: string, limit: number) => {
    await api.updateCommunity(id, { dailyLimit: Math.max(0, limit) });
    await communitiesQuery.refetch();
  };

  const updateMaxPostAgeDays = async (id: string, days: number) => {
    await api.updateCommunity(id, { maxPostAgeDays: Math.max(0, days) });
    await communitiesQuery.refetch();
  };

  const toggleStatus = async (id: string) => {
    const current = communities.find((c) => c.id === id);
    if (!current) return;
    await api.updateCommunity(id, { status: current.status === "active" ? "paused" : "active" });
    await communitiesQuery.refetch();
  };

  // Join tab mock handlers
  const handleCreateJoinJob = () => {
    const urls = joinUrls
      .split("\n")
      .map((u) => u.trim())
      .filter(Boolean);
    if (urls.length === 0) {
      toast.error("Add at least one URL");
      return;
    }
    const profileIds = joinAllProfiles ? profiles.map((p) => p.id) : joinProfileIds;
    if (profileIds.length === 0) {
      toast.error("Select at least one profile");
      return;
    }
    const rows: { profile: string; url: string; status: string; attempts: number; reason: string }[] = [];
    for (const p of profiles.filter((pr) => profileIds.includes(pr.id))) {
      for (const url of urls) {
        rows.push({ profile: p.name, url, status: "pending", attempts: 0, reason: "" });
      }
    }
    setMockJob({
      id: "mock-" + Date.now(),
      completed: 0,
      total: rows.length,
      rows,
      paused: false,
    });
    toast.info("Join job created (mock)");
  };

  const handlePauseJob = () => mockJob && setMockJob({ ...mockJob, paused: true });
  const handleResumeJob = () => mockJob && setMockJob({ ...mockJob, paused: false });
  const handleCancelJob = () => {
    setMockJob(null);
    toast.info("Job cancelled (mock)");
  };

  const fetching = !!fetchStatus?.running;
  const fetchProgressText = fetching
    ? `(${fetchStatus?.profilesDone ?? 0}/${fetchStatus?.profilesTotal ?? 0}) ${fetchStatus?.currentProfileName ? `- ${fetchStatus.currentProfileName}` : ""}`
    : "";

  return (
    <div className="p-4 md:p-6 lg:p-8 pt-16 md:pt-6 lg:pt-8 max-w-7xl">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Communities</h1>
          <p className="text-sm text-muted-foreground mt-1">Groups being monitored — each has its own daily action limit</p>
        </div>
      </div>

      <Tabs defaultValue="joined" className="w-full">
        <TabsList className="mb-4">
          <TabsTrigger value="joined">Joined</TabsTrigger>
          <TabsTrigger value="join">Join</TabsTrigger>
        </TabsList>

        <TabsContent value="joined" className="mt-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
            <div className="flex flex-wrap items-center gap-2">
              <select value={viewProfile} onChange={e => setViewProfile(e.target.value)} className="text-sm px-3 py-2.5 rounded-lg border border-border bg-card text-foreground">
                <option value="">All Profiles</option>
                {profiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
              <button
                onClick={handleFetch}
                disabled={fetching}
                className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg border border-border bg-card text-sm font-medium text-foreground hover:bg-muted transition-colors disabled:opacity-50"
              >
                <RefreshCw className={`w-4 h-4 ${fetching ? 'animate-spin' : ''}`} /> Fetch Communities {fetchProgressText}
              </button>
              <button onClick={() => setShowAdd(true)} className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors">
                <Plus className="w-4 h-4" /> Add Community
              </button>
            </div>
          </div>

          {fetchStatus?.lastResult && (
            <div className="mb-6 p-4 rounded-xl border border-border bg-muted/30">
              <h3 className="text-sm font-semibold text-foreground mb-3">Last fetch</h3>
              <div className="flex flex-wrap gap-4 text-xs">
                {fetchStatus.lastResult.totalFetched != null && (
                  <span className="text-muted-foreground">Total fetched: {fetchStatus.lastResult.totalFetched}</span>
                )}
                {fetchStatus.lastResult.totalJoined != null && (
                  <span className="text-muted-foreground">Joined: {fetchStatus.lastResult.totalJoined}</span>
                )}
                {fetchStatus.lastResult.totalPendingExcluded != null && (
                  <span className="text-muted-foreground">Pending excluded: {fetchStatus.lastResult.totalPendingExcluded}</span>
                )}
                {fetchStatus.lastResult.totalUnknownExcluded != null && (
                  <span className="text-muted-foreground">Unknown excluded: {fetchStatus.lastResult.totalUnknownExcluded}</span>
                )}
              </div>
              <div className="mt-3 space-y-2">
                {fetchStatus.lastResult.results.map((r) => (
                  <div key={r.profileId} className="flex flex-wrap items-center gap-3 text-sm">
                    <span className="font-medium text-foreground">{r.profileName}</span>
                    {r.error?.toLowerCase().includes("no cookies") || r.error?.toLowerCase().includes("not logged in") || r.error?.toLowerCase().includes("cookie") || r.error?.toLowerCase().includes("expired") || r.error?.toLowerCase().includes("unauthorized") ? (
                      <span className="flex items-center gap-2">
                        <span className="px-2 py-0.5 rounded-md bg-amber-500/20 text-amber-700 dark:text-amber-400 text-xs">Login required</span>
                        <button
                          onClick={() => handleProfileLogin(r.profileId, r.profileName)}
                          disabled={!!loginInProgress}
                          className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-medium bg-primary/20 text-primary hover:bg-primary/30 disabled:opacity-50"
                        >
                          <LogIn className="w-3 h-3" /> Login
                        </button>
                      </span>
                    ) : (
                      <span className="text-muted-foreground">
                        total: {r.total ?? "—"} · joined: {r.joined ?? "—"} · pendingExcluded: {r.pendingExcluded ?? "—"} · unknownExcluded: {r.unknownExcluded ?? "—"}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {grouped.map(({ profile, communities }) => (
            <div key={profile.id} className="mb-8">
              <h2 className="text-sm font-semibold text-foreground mb-3 flex items-center gap-2">
                <span className="flex items-center justify-center w-6 h-6 rounded-full bg-primary/10 text-primary text-[10px] font-semibold">{profile.avatar}</span>
                {profile.name}
              </h2>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {communities.map(c => (
                  <div key={c.id} className="bg-card border border-border rounded-xl p-5">
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-2">
                        <Globe className="w-4 h-4 text-muted-foreground" />
                        <h3 className="text-sm font-semibold text-foreground">{c.name}</h3>
                      </div>
                      <button
                        onClick={() => toggleStatus(c.id)}
                        className={`relative w-9 h-5 rounded-full transition-colors ${c.status === 'active' ? 'bg-primary' : 'bg-muted'}`}
                      >
                        <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-card rounded-full shadow transition-transform ${c.status === 'active' ? 'translate-x-4' : ''}`} />
                      </button>
                    </div>
                    <p className="text-xs text-muted-foreground truncate mb-3">{c.url}</p>
                    <div className="grid grid-cols-2 gap-2 text-center mb-2">
                      <div className="bg-muted/50 rounded-lg py-2">
                        <p className="text-sm font-semibold text-foreground">{c.matchesToday}</p>
                        <p className="text-[10px] text-muted-foreground">Matches today</p>
                      </div>
                      <div className="bg-muted/50 rounded-lg py-2">
                        <p className="text-sm font-semibold text-foreground">{c.actionsToday}</p>
                        <p className="text-[10px] text-muted-foreground">Actions today</p>
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-2 text-center">
                      <div className="bg-muted/50 rounded-lg py-2">
                        <div className="flex items-center justify-center gap-1">
                          <input
                            type="number"
                            value={c.dailyLimit}
                            onChange={e => updateLimit(c.id, +e.target.value)}
                            className="w-12 text-center text-sm font-semibold text-foreground bg-transparent border-none focus:outline-none focus:ring-0"
                          />
                        </div>
                        <p className="text-[10px] text-muted-foreground">Daily limit</p>
                      </div>
                      <div className="bg-muted/50 rounded-lg py-2">
                        <div className="flex items-center justify-center gap-1">
                          <input
                            type="number"
                            min={0}
                            value={c.maxPostAgeDays}
                            onChange={e => updateMaxPostAgeDays(c.id, Number(e.target.value))}
                            className="w-12 text-center text-sm font-semibold text-foreground bg-transparent border-none focus:outline-none focus:ring-0"
                          />
                        </div>
                        <p className="text-[10px] text-muted-foreground">Max age (days)</p>
                      </div>
                    </div>
                    <div className="grid grid-cols-1 gap-2 text-center mt-2">
                      <div className="bg-muted/50 rounded-lg py-2">
                        <p className="text-xs font-medium text-foreground">{c.lastScanned}</p>
                        <p className="text-[10px] text-muted-foreground">Last scanned</p>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}

          {showAdd && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/20 animate-fade-in" onClick={() => setShowAdd(false)}>
              <div className="bg-card border border-border rounded-2xl w-full max-w-md p-6 shadow-xl animate-count-up" onClick={e => e.stopPropagation()}>
                <div className="flex items-center justify-between mb-5">
                  <h3 className="text-lg font-semibold text-foreground">Add Community</h3>
                  <button onClick={() => setShowAdd(false)} className="p-1 rounded-md hover:bg-muted"><X className="w-4 h-4" /></button>
                </div>
                <div className="space-y-4">
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">Community Name</label>
                    <input value={newName} onChange={e => setNewName(e.target.value)} className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring" placeholder="e.g. SaaS Growth Hackers" />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">URL</label>
                    <input value={newUrl} onChange={e => setNewUrl(e.target.value)} className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring" placeholder="https://facebook.com/groups/..." />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">Assign to Profile</label>
                    <select value={newProfile} onChange={e => setNewProfile(e.target.value)} className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground">
                      {profiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">Daily Limit</label>
                    <input type="number" min={0} value={newDailyLimit} onChange={e => setNewDailyLimit(Number(e.target.value))} className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground mb-1 block">Max Post Age (days)</label>
                    <input type="number" min={0} value={newMaxPostAgeDays} onChange={e => setNewMaxPostAgeDays(Number(e.target.value))} className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
                  </div>
                  <button onClick={handleAdd} className="w-full py-2.5 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors">Add Community</button>
                </div>
              </div>
            </div>
          )}
        </TabsContent>

        <TabsContent value="join" className="mt-4">
          <div className="space-y-6">
            <div className="bg-card border border-border rounded-xl p-6">
              <h3 className="text-sm font-semibold text-foreground mb-4">Upload list</h3>
              <div className="space-y-4">
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">CSV file (optional)</label>
                  <input
                    type="file"
                    accept=".csv"
                    className="w-full text-sm text-foreground file:mr-2 file:py-2 file:px-4 file:rounded-lg file:border-0 file:bg-primary file:text-primary-foreground file:text-sm file:font-medium"
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">Community URLs (one per line)</label>
                  <textarea
                    value={joinUrls}
                    onChange={e => setJoinUrls(e.target.value)}
                    placeholder="https://facebook.com/groups/xxx\nhttps://facebook.com/groups/yyy"
                    rows={5}
                    className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring placeholder:text-muted-foreground"
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">Profiles</label>
                  <div className="flex items-center gap-3 mb-2">
                    <label className="flex items-center gap-2 text-sm cursor-pointer">
                      <input type="checkbox" checked={joinAllProfiles} onChange={e => setJoinAllProfiles(e.target.checked)} className="rounded border-border" />
                      All profiles
                    </label>
                  </div>
                  {!joinAllProfiles && (
                    <div className="flex flex-wrap gap-2">
                      {profiles.map((p) => (
                        <label key={p.id} className="flex items-center gap-2 px-3 py-2 rounded-lg border border-border bg-card text-sm cursor-pointer hover:bg-muted/50">
                          <input
                            type="checkbox"
                            checked={joinProfileIds.includes(p.id)}
                            onChange={e => {
                              if (e.target.checked) setJoinProfileIds((prev) => [...prev, p.id]);
                              else setJoinProfileIds((prev) => prev.filter((id) => id !== p.id));
                            }}
                            className="rounded border-border"
                          />
                          {p.name}
                        </label>
                      ))}
                    </div>
                  )}
                </div>
                <button
                  onClick={handleCreateJoinJob}
                  className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
                >
                  <Upload className="w-4 h-4" /> Create Join Job
                </button>
              </div>
            </div>

            {mockJob && (
              <div className="bg-card border border-border rounded-xl p-6">
                <h3 className="text-sm font-semibold text-foreground mb-4">Active job</h3>
                <div className="mb-4">
                  <div className="flex justify-between text-xs text-muted-foreground mb-1">
                    <span>{mockJob.completed} / {mockJob.total}</span>
                  </div>
                  <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                    <div
                      className="h-full bg-primary transition-all"
                      style={{ width: `${mockJob.total ? (mockJob.completed / mockJob.total) * 100 : 0}%` }}
                    />
                  </div>
                </div>
                <div className="overflow-x-auto rounded-lg border border-border">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border bg-muted/30">
                        <th className="text-left px-3 py-2 font-medium text-foreground">Profile</th>
                        <th className="text-left px-3 py-2 font-medium text-foreground">Community URL</th>
                        <th className="text-left px-3 py-2 font-medium text-foreground">Status</th>
                        <th className="text-left px-3 py-2 font-medium text-foreground">Attempts</th>
                        <th className="text-left px-3 py-2 font-medium text-foreground">Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {mockJob.rows.slice(0, 10).map((row, i) => (
                        <tr key={i} className="border-b border-border last:border-0">
                          <td className="px-3 py-2 text-foreground">{row.profile}</td>
                          <td className="px-3 py-2 text-muted-foreground truncate max-w-[200px]">{row.url}</td>
                          <td className="px-3 py-2 text-foreground">{row.status}</td>
                          <td className="px-3 py-2 text-foreground">{row.attempts}</td>
                          <td className="px-3 py-2 text-muted-foreground">{row.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {mockJob.rows.length > 10 && (
                    <p className="text-xs text-muted-foreground px-3 py-2 border-t border-border">+{mockJob.rows.length - 10} more rows</p>
                  )}
                </div>
                <div className="flex flex-wrap gap-2 mt-4">
                  <button
                    onClick={handlePauseJob}
                    disabled={mockJob.paused}
                    className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-border bg-card text-sm font-medium text-foreground hover:bg-muted disabled:opacity-50"
                  >
                    <Pause className="w-4 h-4" /> Pause
                  </button>
                  <button
                    onClick={handleResumeJob}
                    disabled={!mockJob.paused}
                    className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-border bg-card text-sm font-medium text-foreground hover:bg-muted disabled:opacity-50"
                  >
                    <Play className="w-4 h-4" /> Resume
                  </button>
                  <button
                    onClick={handleCancelJob}
                    className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-destructive/50 bg-destructive/10 text-destructive text-sm font-medium hover:bg-destructive/20"
                  >
                    <X className="w-4 h-4" /> Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
