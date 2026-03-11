import { useEffect, useMemo, useRef, useState } from "react";
import { useAutomationSettings, useCommunities, useProfiles, useQueue } from "@/hooks/useEngageFlow";
import { ApiError, api } from "@/lib/api";
import type { Community, Profile } from "@/lib/types";
import { X, Plus, Upload, Pause, Play, Trash2, RotateCcw, RefreshCw, Pencil, Search, Wifi, WifiOff, Shield, Loader2, Clock } from "lucide-react";
import { toast } from "sonner";

const statusColors: Record<string, string> = {
  running: "bg-success",
  paused: "bg-warning",
  idle: "bg-muted-foreground/40",
  checking: "bg-info",
  queued: "bg-muted-foreground/70",
  ready: "bg-success",
  blocked: "bg-destructive",
  captcha: "bg-warning",
  logged_out: "bg-destructive",
  disconnected: "bg-destructive",
};

const statusLabels: Record<string, string> = {
  running: "Running",
  paused: "Paused",
  idle: "Idle",
  checking: "Checking...",
  queued: "Queued",
  ready: "Ready",
  blocked: "Blocked",
  captcha: "Captcha",
  logged_out: "Logged Out",
  disconnected: "Disconnected",
};

export default function ProfilesPage() {
  const profilesQuery = useProfiles();
  const communitiesQuery = useCommunities();
  const queueQuery = useQueue();
  const automationSettingsQuery = useAutomationSettings();
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [showAddModal, setShowAddModal] = useState(false);
  const [addProfilePending, setAddProfilePending] = useState(false);
  const [newProfile, setNewProfile] = useState({ name: "", password: "", email: "", proxy: "" });
  const [editMode, setEditMode] = useState(false);
  const [editFields, setEditFields] = useState({ name: "", password: "", email: "", proxy: "" });
  const [communities, setCommunities] = useState<Community[]>([]);
  const [checkLoginPending, setCheckLoginPending] = useState<Record<string, boolean>>({});
  const [checkLoginMessage, setCheckLoginMessage] = useState<Record<string, string>>({});
  const [checkProxyPending, setCheckProxyPending] = useState<Record<string, boolean>>({});
  const [checkProxyMessage, setCheckProxyMessage] = useState<Record<string, string>>({});
  const fileRef = useRef<HTMLInputElement>(null);
  const settings = automationSettingsQuery.data;
  const selected = profiles.find(p => p.id === selectedProfileId);
  const dailyCap = settings?.globalDailyCapPerAccount ?? 0;
  const profileCommunities = selected ? communities.filter(c => c.profileId === selected.id) : [];
  const queueItems = queueQuery.data ?? [];

  const nextActionByProfileId = useMemo(() => {
    const parseQueueTs = (scheduledFor: string, scheduledTime: string): number => {
      const iso = Date.parse(String(scheduledFor || "").trim());
      if (!Number.isNaN(iso)) return iso;
      const t = String(scheduledTime || "").trim();
      const tm = t.match(/^(\d{1,2}):(\d{2})(?:\s*(AM|PM))?$/i);
      if (!tm) return Number.POSITIVE_INFINITY;
      let hour = Number(tm[1]);
      const minute = Number(tm[2]);
      const meridiem = String(tm[3] || "").toUpperCase();
      if (meridiem === "PM" && hour < 12) hour += 12;
      if (meridiem === "AM" && hour === 12) hour = 0;
      const now = new Date();
      return new Date(now.getFullYear(), now.getMonth(), now.getDate(), hour, minute, 0, 0).getTime();
    };

    const grouped = new Map<string, { id: string; text: string; ts: number }>();
    for (const item of queueItems) {
      const profileId = String(item.profileId || "").trim();
      if (!profileId) continue;
      const ts = parseQueueTs(String(item.scheduledFor || ""), String(item.scheduledTime || ""));
      const text = `${item.scheduledTime || "--:--"} · ${item.community || "Community"} · ${item.keyword || "keyword"}`;
      const current = grouped.get(profileId);
      if (!current || ts < current.ts) {
        grouped.set(profileId, { id: String(item.id || "").trim(), text, ts });
      }
    }
    return grouped;
  }, [queueItems]);

  useEffect(() => {
    if (!profilesQuery.data) return;
    setProfiles(profilesQuery.data);
  }, [profilesQuery.data]);

  useEffect(() => {
    if (communitiesQuery.data) {
      setCommunities(communitiesQuery.data);
    }
  }, [communitiesQuery.data]);

  useEffect(() => {
    setEditMode(false);
  }, [selectedProfileId]);

  const refreshAll = async () => {
    await Promise.all([profilesQuery.refetch(), communitiesQuery.refetch(), queueQuery.refetch()]);
  };

  const handleAddProfile = async () => {
    if (!newProfile.email.trim() || !newProfile.password.trim()) return;
    setAddProfilePending(true);
    try {
      const name = newProfile.name.trim() || newProfile.email.trim().split("@")[0];
      const avatar = name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2);
      const created = await api.createProfile({
        name,
        password: newProfile.password,
        email: newProfile.email || undefined,
        proxy: newProfile.proxy || undefined,
        avatar,
        status: 'checking',
        dailyUsage: 0,
        groupsConnected: 0,
      });
      setNewProfile({ name: "", password: "", email: "", proxy: "" });
      setShowAddModal(false);
      setCheckLoginPending((prev) => ({ ...prev, [created.id]: true }));
      setCheckLoginMessage((prev) => ({ ...prev, [created.id]: "checking: Login check in progress..." }));
      await refreshAll();

      void (async () => {
        try {
          const loginResult = await api.profileCheckLogin(created.id);
          setCheckLoginMessage((prev) => ({ ...prev, [created.id]: `${loginResult.status}: ${loginResult.message}` }));
          toast.message(loginResult.message);
        } catch (error) {
          const message = error instanceof ApiError ? error.message : "Login check failed";
          setCheckLoginMessage((prev) => ({ ...prev, [created.id]: message }));
          toast.error(message);
        } finally {
          setCheckLoginPending((prev) => ({ ...prev, [created.id]: false }));
          await refreshAll();
        }
      })();
    } finally {
      setAddProfilePending(false);
    }
  };

  const startEdit = () => {
    if (!selected) return;
    setEditFields({ name: selected.name, password: selected.password || "", email: selected.email || "", proxy: selected.proxy || "" });
    setEditMode(true);
  };

  const saveEdit = async () => {
    if (!selected) return;
    const name = editFields.name.trim() || editFields.email.trim().split("@")[0] || selected.name;
    const avatar = name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2);
    try {
      await api.updateProfile(selected.id, {
        name,
        password: editFields.password,
        email: editFields.email || undefined,
        // Keep proxy key in payload so clearing input actually clears saved proxy.
        proxy: editFields.proxy.trim(),
        avatar,
      });
      setEditMode(false);
      await refreshAll();
      toast.success("Profile updated");
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "Failed to update profile";
      toast.error(message);
    }
  };

  const handleCSVImport = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = ev.target?.result as string;
      const lines = text.split('\n').filter(l => l.trim());
      if (lines.length < 2) return;
      const headers = lines[0].toLowerCase().split(',').map(h => h.trim());
      const emailIdx = headers.indexOf('email');
      const passwordIdx = headers.indexOf('password');
      const nameIdx = headers.indexOf('name');
      const proxyIdx = headers.indexOf('proxy');
      if (emailIdx === -1 || passwordIdx === -1) return;

      const newProfiles: Omit<Profile, "id">[] = lines.slice(1).map((line) => {
        const cols = line.split(',').map(c => c.trim());
        const email = cols[emailIdx] || '';
        const password = cols[passwordIdx] || '';
        const name = nameIdx >= 0 ? cols[nameIdx] || email : email;
        const proxy = proxyIdx >= 0 ? cols[proxyIdx] || '' : '';
        const avatar = name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2);
        return {
          name,
          email,
          password,
          proxy: proxy || undefined,
          avatar,
          status: 'checking' as const,
          dailyUsage: 0,
          groupsConnected: 0,
        };
      }).filter(p => p.email);

      void (async () => {
        for (const profile of newProfiles) {
          await api.createProfile(profile);
        }
        await refreshAll();
      })().catch((error) => {
        const message = error instanceof ApiError ? error.message : "CSV import failed";
        toast.error(message);
      });
    };
    reader.readAsText(file);
    e.target.value = '';
  };

  const togglePause = async (id: string) => {
    const profile = profiles.find((p) => p.id === id);
    if (!profile) return;
    await api.updateProfile(id, { status: profile.status === "paused" ? "running" : "paused" });
    await refreshAll();
  };

  const deleteProfile = async (id: string) => {
    const target = profiles.find((p) => p.id === id);
    const targetName = target?.name || "this profile";
    const ok = window.confirm(`Delete "${targetName}"? This action cannot be undone.`);
    if (!ok) return;
    await api.deleteProfile(id);
    await refreshAll();
    if (selectedProfileId === id) setSelectedProfileId(null);
  };

  const resetCounter = async (id: string) => {
    await api.profileResetCounters(id);
    await refreshAll();
  };

  const handleCheckLogin = async (profileId: string) => {
    if (checkLoginPending[profileId]) return;
    setCheckLoginPending((prev) => ({ ...prev, [profileId]: true }));
    try {
      const result = await api.profileCheckLogin(profileId);
      setCheckLoginMessage((prev) => ({ ...prev, [profileId]: `${result.status}: ${result.message}` }));
      toast.message(result.message);
      await refreshAll();
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "Login check failed";
      setCheckLoginMessage((prev) => ({ ...prev, [profileId]: message }));
      toast.error(message);
    } finally {
      setCheckLoginPending((prev) => ({ ...prev, [profileId]: false }));
    }
  };

  const handleCheckProxy = async (profileId: string) => {
    if (checkProxyPending[profileId]) return;
    setCheckProxyPending((prev) => ({ ...prev, [profileId]: true }));
    try {
      const result = await api.profileCheckProxy(profileId);
      const targetProfile = profiles.find((item) => item.id === profileId);
      const targetProxy = (targetProfile?.proxy || "").trim().toLowerCase();
      if (result.status === "connected" && targetProxy) {
        setCheckProxyMessage((prev) => {
          const next = { ...prev };
          for (const item of profiles) {
            if ((item.proxy || "").trim().toLowerCase() === targetProxy) {
              next[item.id] = `${result.status}: ${result.message}`;
            }
          }
          return next;
        });
      } else {
        setCheckProxyMessage((prev) => ({ ...prev, [profileId]: `${result.status}: ${result.message}` }));
      }
      toast.message(result.message);
      await refreshAll();
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "Proxy check failed";
      setCheckProxyMessage((prev) => ({ ...prev, [profileId]: `proxy_error: ${message}` }));
      toast.error(message);
    } finally {
      setCheckProxyPending((prev) => ({ ...prev, [profileId]: false }));
    }
  };

  const getCheckStatus = (profileId: string): string | null => {
    const raw = checkLoginMessage[profileId];
    if (!raw) return null;
    const idx = raw.indexOf(":");
    if (idx <= 0) return null;
    return raw.slice(0, idx).trim();
  };

  const getCheckText = (profileId: string): string | null => {
    const raw = checkLoginMessage[profileId];
    if (!raw) return null;
    const idx = raw.indexOf(":");
    if (idx <= 0) return raw;
    return raw.slice(idx + 1).trim();
  };

  const getProxyStatus = (profileId: string): string | null => {
    const raw = checkProxyMessage[profileId];
    if (raw) {
      const idx = raw.indexOf(":");
      if (idx > 0) return raw.slice(0, idx).trim();
    }
    const profile = profiles.find((item) => item.id === profileId);
    return profile?.proxyStatus || null;
  };

  const getProxyText = (profileId: string): string | null => {
    const raw = checkProxyMessage[profileId];
    if (raw) {
      const idx = raw.indexOf(":");
      if (idx <= 0) return raw;
      return raw.slice(idx + 1).trim();
    }
    const profile = profiles.find((item) => item.id === profileId);
    if (profile?.proxyStatus === "connected") return "Connected (cached)";
    if (profile?.proxyStatus === "disconnected") return "Disconnected (cached)";
    return null;
  };

  return (
    <div className="p-4 md:p-6 lg:p-8 pt-16 md:pt-6 lg:pt-8 max-w-7xl">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Profiles</h1>
          <p className="text-sm text-muted-foreground mt-1">Connected profiles — timing and caps are managed globally in Automation Settings</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <input type="text" placeholder="Search profiles..." value={searchQuery} onChange={e => setSearchQuery(e.target.value)}
              className="pl-9 pr-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring w-48" />
          </div>
          <input ref={fileRef} type="file" accept=".csv" onChange={handleCSVImport} className="hidden" />
          <button onClick={() => fileRef.current?.click()} className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border text-sm font-medium text-foreground hover:bg-muted transition-colors">
            <Upload className="w-4 h-4" /> Import CSV
          </button>
          <button onClick={() => setShowAddModal(true)} className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors">
            <Plus className="w-4 h-4" /> Add Profile
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {profiles.filter(p => !searchQuery || p.name.toLowerCase().includes(searchQuery.toLowerCase()) || (p.email || "").toLowerCase().includes(searchQuery.toLowerCase())).map(profile => {
          const displayStatus = checkLoginPending[profile.id] ? "checking" : profile.status;
          const checkStatus = getCheckStatus(profile.id);
          const checkText = getCheckText(profile.id);
          const proxyStatus = getProxyStatus(profile.id);
          const proxyText = getProxyText(profile.id);
          const proxyState = !profile.proxy
            ? "none"
            : checkProxyPending[profile.id]
              ? "checking"
              : (proxyStatus === "proxy_error" || proxyStatus === "network_error")
                ? "disconnected"
                : (proxyStatus === "connected")
                  ? "connected"
                  : "configured";
          const proxyRetryAvailable =
            !!profile.proxy &&
            (proxyStatus === "proxy_error" || proxyStatus === "network_error");
          return (
            <div key={profile.id} className="bg-card border border-border rounded-xl p-5 hover:border-primary/30 hover:shadow-sm transition-all h-[270px] flex flex-col">
              <button onClick={() => setSelectedProfileId(profile.id)} className="w-full text-left">
                <div className="flex items-center gap-3 mb-4">
                  <div className="flex items-center justify-center w-10 h-10 rounded-full bg-primary/10 text-primary text-sm font-semibold">
                    {profile.avatar}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <p className="text-sm font-semibold text-foreground truncate">{profile.name}</p>
                      {profile.source === "micro" && (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-primary/20 text-primary">Micro</span>
                      )}
                    </div>
                    <div className="flex items-center gap-1.5 mt-0.5">
                      <span className={`w-2 h-2 rounded-full ${statusColors[displayStatus]} ${displayStatus === 'running' ? 'animate-pulse-dot' : ''}`} />
                      <span className="text-xs text-muted-foreground">{statusLabels[displayStatus] || displayStatus}</span>
                    </div>
                  </div>
                </div>

                <div className="mb-3">
                  <div className="flex justify-between text-xs text-muted-foreground mb-1">
                    <span>Daily usage</span>
                    <span>{profile.dailyUsage} / {dailyCap || "-"}</span>
                  </div>
                  <div className="h-1.5 bg-muted/80 rounded-full overflow-hidden">
                    <div className="h-full bg-primary rounded-full transition-all" style={{ width: `${dailyCap ? (profile.dailyUsage / dailyCap) * 100 : 0}%` }} />
                  </div>
                </div>

                <div className="flex justify-between text-xs text-muted-foreground mb-2">
                  <span>{profile.groupsConnected} groups</span>
                  {profile.connected_at && (
                    <span>Added {new Date(profile.connected_at).toLocaleDateString()}</span>
                  )}
                </div>
                {nextActionByProfileId.get(profile.id)?.text && (
                  <div className="h-5 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                    <Clock className="w-3 h-3 shrink-0" />
                    <span className="truncate">
                      {`Next: ${nextActionByProfileId.get(profile.id)?.text}`}
                    </span>
                  </div>
                )}
              </button>

              {/* Quick actions */}
              <div className="flex items-center gap-1 mt-3 pt-3 border-t border-border">
                <button onClick={() => togglePause(profile.id)} className="p-1.5 rounded-md hover:bg-muted transition-colors" title={profile.status === 'paused' ? 'Resume' : 'Pause'}>
                  {profile.status === 'paused' ? <Play className="w-3.5 h-3.5 text-success" /> : <Pause className="w-3.5 h-3.5 text-warning" />}
                </button>
                <button onClick={() => resetCounter(profile.id)} className="p-1.5 rounded-md hover:bg-muted transition-colors" title="Reset daily counter">
                  <RotateCcw className="w-3.5 h-3.5 text-muted-foreground" />
                </button>
                <button className="p-1.5 rounded-md hover:bg-muted transition-colors" title="Force re-scan">
                  <RefreshCw className="w-3.5 h-3.5 text-muted-foreground" />
                </button>
                <div className="flex-1" />
                <button onClick={() => deleteProfile(profile.id)} className="p-1.5 rounded-md hover:bg-muted transition-colors" title="Delete profile">
                  <Trash2 className="w-3.5 h-3.5 text-destructive" />
                </button>
              </div>

              {profile.proxy && (
                <div className="mt-2 pt-2 border-t border-border">
                  <div className="flex items-center gap-2">
                    {proxyState === "checking" && <Loader2 className="w-3 h-3 text-muted-foreground animate-spin" />}
                    {proxyState === "connected" && <Wifi className="w-3 h-3 text-success" />}
                    {proxyState === "disconnected" && <WifiOff className="w-3 h-3 text-destructive" />}
                    {proxyState === "configured" && <Shield className="w-3 h-3 text-muted-foreground" />}
                    <span className={`text-[11px] ${
                      proxyState === "connected"
                        ? "text-success"
                        : proxyState === "disconnected"
                          ? "text-destructive"
                          : "text-muted-foreground"
                    }`}>
                      {proxyState === "checking" && "Checking..."}
                      {proxyState === "connected" && "Connected"}
                      {proxyState === "disconnected" && "Disconnected"}
                      {proxyState === "configured" && "Configured"}
                    </span>
                    {proxyRetryAvailable ? (
                      <button
                        onClick={() => handleCheckProxy(profile.id)}
                        disabled={!!checkProxyPending[profile.id]}
                        className="ml-auto text-[11px] text-primary hover:text-primary/80 font-medium disabled:opacity-60"
                      >
                        {checkProxyPending[profile.id] ? "Retrying..." : "Retry"}
                      </button>
                    ) : (
                      <button
                        onClick={() => handleCheckProxy(profile.id)}
                        disabled={!!checkProxyPending[profile.id]}
                        className="ml-auto inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors disabled:opacity-60"
                      >
                        <Shield className="w-3 h-3" />
                        Test Proxy
                      </button>
                    )}
                  </div>
                  <p className="text-[11px] text-muted-foreground mt-1 truncate">Proxy: {profile.proxy}</p>
                </div>
              )}
              {proxyRetryAvailable && (
                <div className="mt-2 flex items-center gap-2">
                  <span className="text-[11px] text-destructive truncate">{proxyText || "Proxy connection failed"}</span>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Profile Detail Panel */}
      {selected && (
        <div className="fixed inset-0 z-50 flex justify-end bg-foreground/20 animate-fade-in" onClick={() => setSelectedProfileId(null)}>
          <div className="w-full max-w-md bg-card border-l border-border h-full overflow-y-auto animate-slide-in" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-6 py-5 border-b border-border">
              <h2 className="text-lg font-semibold text-foreground">{selected.name}</h2>
              <button onClick={() => setSelectedProfileId(null)} className="p-1 rounded-md hover:bg-muted transition-colors">
                <X className="w-5 h-5 text-muted-foreground" />
              </button>
            </div>

            <div className="p-6 space-y-6">
              {/* Status */}
              <div>
                <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Status</h4>
                <div className="flex items-center gap-2">
                  <span className={`w-2.5 h-2.5 rounded-full ${statusColors[checkLoginPending[selected.id] ? 'checking' : selected.status]}`} />
                  <span className="text-sm font-medium text-foreground">{statusLabels[checkLoginPending[selected.id] ? 'checking' : selected.status]}</span>
                </div>
              </div>

              {/* Account Details */}
              <div>
                <div className="flex items-center justify-between mb-3">
                  <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Account Details</h4>
                  {!editMode && (
                    <button onClick={startEdit} className="inline-flex items-center gap-1 text-xs text-primary hover:text-primary/80 transition-colors">
                      <Pencil className="w-3 h-3" /> Edit
                    </button>
                  )}
                </div>
                {editMode ? (
                  <div className="space-y-3">
                    <div>
                      <label className="text-xs text-muted-foreground mb-1 block">Name</label>
                      <input type="text" value={editFields.name} onChange={e => setEditFields(f => ({ ...f, name: e.target.value }))}
                        className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground mb-1 block">Email</label>
                      <input type="email" value={editFields.email} onChange={e => setEditFields(f => ({ ...f, email: e.target.value }))} placeholder="email@example.com"
                        className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground mb-1 block">Password</label>
                      <input type="text" value={editFields.password} onChange={e => setEditFields(f => ({ ...f, password: e.target.value }))}
                        className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground mb-1 block">Proxy</label>
                      <input type="text" value={editFields.proxy} onChange={e => setEditFields(f => ({ ...f, proxy: e.target.value }))} placeholder="user:pass@host:port or host:port"
                        className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground font-mono text-xs focus:outline-none focus:ring-2 focus:ring-ring" />
                    </div>
                    <div className="flex gap-2">
                      <button onClick={saveEdit} className="px-3 py-1.5 rounded-md bg-primary text-primary-foreground text-xs font-medium hover:bg-primary/90 transition-colors">Save</button>
                      <button onClick={() => setEditMode(false)} className="px-3 py-1.5 rounded-md border border-border text-xs font-medium text-foreground hover:bg-muted transition-colors">Cancel</button>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-2 text-sm">
                    {selected.email && (
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">Email</span>
                        <span className="text-foreground font-medium">{selected.email}</span>
                      </div>
                    )}
                    {selected.proxy && (
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">Proxy</span>
                        <span className="text-foreground font-medium font-mono text-xs">{selected.proxy}</span>
                      </div>
                    )}
                    {selected.password && (
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">Password</span>
                        <span className="text-foreground font-medium">{selected.password}</span>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Quick Actions */}
              <div>
                <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Quick Actions</h4>
                <div className="flex flex-wrap gap-2">
                  <button onClick={() => handleCheckLogin(selected.id)} disabled={!!checkLoginPending[selected.id]} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border text-xs font-medium text-foreground hover:bg-muted transition-colors disabled:opacity-60">
                    {checkLoginPending[selected.id] ? "Checking..." : "Check Login"}
                  </button>
                  <button onClick={() => togglePause(selected.id)} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border text-xs font-medium text-foreground hover:bg-muted transition-colors">
                    {selected.status === 'paused' ? <><Play className="w-3 h-3 text-success" /> Resume</> : <><Pause className="w-3 h-3 text-warning" /> Pause</>}
                  </button>
                  <button onClick={() => resetCounter(selected.id)} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border text-xs font-medium text-foreground hover:bg-muted transition-colors">
                    <RotateCcw className="w-3 h-3" /> Reset Counter
                  </button>
                  <button className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border text-xs font-medium text-foreground hover:bg-muted transition-colors">
                    <RefreshCw className="w-3 h-3" /> Force Re-scan
                  </button>
                  <button onClick={() => window.open('https://www.skool.com/settings?t=profile', '_blank')} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border text-xs font-medium text-foreground hover:bg-muted transition-colors" title="Open Skool profile settings">
                    <Pencil className="w-3 h-3" /> Edit profile
                  </button>
                  <button onClick={() => deleteProfile(selected.id)} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-destructive text-xs font-medium text-destructive hover:bg-destructive/10 transition-colors">
                    <Trash2 className="w-3 h-3" /> Delete
                  </button>
                </div>
                {checkLoginMessage[selected.id] && (
                  <p className="text-[11px] text-muted-foreground mt-2">{checkLoginMessage[selected.id]}</p>
                )}
              </div>

              {/* Global Settings Info */}
              <div className="bg-muted/50 rounded-xl p-4">
                <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Inherited Global Settings</h4>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Daily Cap</span>
                    <span className="text-foreground font-medium">{dailyCap || "-"}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Delay</span>
                    <span className="text-foreground font-medium">{settings ? `${settings.delayMin}–${settings.delayMax} min` : "-"}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Run Window</span>
                    <span className="text-foreground font-medium">{settings ? `${settings.runFrom} – ${settings.runTo}` : "-"}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Active Days</span>
                    <span className="text-foreground font-medium">{settings ? settings.activeDays.join(', ') : "-"}</span>
                  </div>
                </div>
                <p className="text-[11px] text-muted-foreground mt-3">All timing is managed in Automation Settings</p>
              </div>

              {/* Usage */}
              <div>
                <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Today's Usage</h4>
                <div className="mb-2">
                  <div className="flex justify-between text-sm mb-1">
                    <span className="text-muted-foreground">Actions</span>
                    <span className="text-foreground font-medium">{selected.dailyUsage} / {dailyCap || "-"}</span>
                  </div>
                  <div className="h-2 bg-muted/80 rounded-full overflow-hidden">
                    <div className="h-full bg-primary rounded-full transition-all" style={{ width: `${dailyCap ? (selected.dailyUsage / dailyCap) * 100 : 0}%` }} />
                  </div>
                </div>
              </div>

              {/* Connected Communities */}
              <div>
                <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Connected Communities ({profileCommunities.length})</h4>
                <div className="space-y-2">
                  {profileCommunities.map(c => (
                    <div key={c.id} className="flex items-center justify-between py-2 px-3 bg-muted/30 rounded-lg">
                      <span className="text-sm text-foreground">{c.name}</span>
                      <span className="text-xs text-muted-foreground">Limit: {c.dailyLimit}/day</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Add Profile Modal */}
      {showAddModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/20 animate-fade-in" onClick={() => setShowAddModal(false)}>
          <div className="bg-card border border-border rounded-2xl w-full max-w-md p-6 shadow-xl animate-count-up" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-5">
              <h3 className="text-lg font-semibold text-foreground">Add Profile</h3>
              <button onClick={() => setShowAddModal(false)} className="p-1 rounded-md hover:bg-muted"><X className="w-4 h-4" /></button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">Profile Name <span className="text-muted-foreground/50">(optional, defaults to email)</span></label>
                <input type="text" value={newProfile.name} onChange={e => setNewProfile(p => ({ ...p, name: e.target.value }))} placeholder="e.g. John Doe"
                  className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">Email <span className="text-destructive">*</span></label>
                <input type="email" value={newProfile.email} onChange={e => setNewProfile(p => ({ ...p, email: e.target.value }))} placeholder="email@example.com"
                  className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">Password <span className="text-destructive">*</span></label>
                <input type="text" value={newProfile.password} onChange={e => setNewProfile(p => ({ ...p, password: e.target.value }))} placeholder="password"
                  className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">Proxy <span className="text-muted-foreground/50">(optional)</span></label>
                <input type="text" value={newProfile.proxy} onChange={e => setNewProfile(p => ({ ...p, proxy: e.target.value }))} placeholder="user:pass@host:port or host:port"
                  className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground font-mono text-xs focus:outline-none focus:ring-2 focus:ring-ring" />
                <p className="text-[11px] text-muted-foreground mt-1">Formats: user:pass@host:port, http://user:pass@host:port, host:port</p>
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-6">
              <button onClick={() => setShowAddModal(false)} className="px-4 py-2 rounded-lg border border-border text-sm font-medium text-foreground hover:bg-muted transition-colors">Cancel</button>
              <button onClick={handleAddProfile} disabled={addProfilePending || !newProfile.email.trim() || !newProfile.password.trim()}
                className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
                {addProfilePending ? "Saving..." : "Save Profile"}
              </button>
            </div>
          </div>
        </div>
      )}

    
      </div>
  );
}


