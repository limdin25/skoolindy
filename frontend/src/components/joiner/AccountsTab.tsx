import { useState, useEffect, useCallback } from "react";
import { Plus, Upload, TestTube, ChevronDown, ChevronUp, Eye, EyeOff, Play, Square, Settings2, Timer, Plug, Trash2, Cookie, Download, XCircle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { StatusBadge } from "./StatusBadge";
import { api } from "./joiner-api";
import { toast } from "sonner";

interface AccountsTabProps {
  onFilterLogs?: (account: string) => void;
}

function formatDateTime(value?: string) {
  if (!value) return '—';
  const d = new Date(value);
  const pad = (n: number) => String(n).padStart(2, '0');
  const dd = pad(d.getDate());
  const mm = pad(d.getMonth() + 1);
  const yyyy = d.getFullYear();
  const hh = pad(d.getHours());
  const min = pad(d.getMinutes());
  const ss = pad(d.getSeconds());
  return `${dd}/${mm}/${yyyy} ${hh}:${min}:${ss}`;
}

function mapAuthStatus(p: any): string {
  if (p.auth_status === 'connected') return 'Connected';
  if (p.auth_status === 'connecting') return 'Connecting';
  if (p.auth_status === 'expired') return 'Expired';
  if (p.auth_status === 'error') return 'Error';
  if (!p.cookie_json) return 'Missing Cookies';
  return 'Disconnected';
}

function mapRunStatus(p: any): string {
  if (p.is_running) return 'Running';
  if (!p.cookie_json) return 'Blocked';
  return 'Idle';
}

function formatCountdown(nextActionAt: string | null, isRunning: boolean): string {
  if (!isRunning) return '—';
  if (!nextActionAt) return 'Working...';
  const diff = Math.max(0, Math.floor((new Date(nextActionAt).getTime() - Date.now()) / 1000));
  if (diff <= 0) return 'Working...';
  const mins = Math.floor(diff / 60);
  const secs = diff % 60;
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function timeAgo(ts: string | null): string {
  if (!ts) return 'Never';
  const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}
function toISODate(mmddyyyy) {
  if (!mmddyyyy || typeof mmddyyyy !== "string") return null;
  const parts = mmddyyyy.trim().split("/");
  if (parts.length !== 3) return null;
  const [m, d, y] = parts;
  const pad = (x) => String(parseInt(x, 10)).padStart(2, "0");
  return y + "-" + pad(m) + "-" + pad(d);
}

function normalizeCommunityForDisplay(c) {
  const raw = c?.name ?? c?.group_name ?? c?.title ?? c?.slug ?? "";
  const str = typeof raw === "string" ? raw : String(raw ?? "");
  let display_name = str.trim();
  let member_since = "—";

  const memberOfMatch = str.match(/member\s+of\s+([^]+?)\s+since\s+(\d{1,2}\/\d{1,2}\/\d{4})/i);
  if (memberOfMatch) {
    display_name = memberOfMatch[1].replace(/©|™|®/g, "").trim();
    member_since = toISODate(memberOfMatch[2]) ?? "—";
  }

  if (/You've been a member of|MembershipNotifications/i.test(str) && !memberOfMatch) {
    const m = str.match(/member\s+of\s+([^]+?)(?:\s+since|$)/i);
    if (m) display_name = m[1].trim();
  }

  if (!display_name || /^\s*$/.test(display_name)) {
    const slug = (c?.slug ?? "").toString();
    display_name = slug ? slug.replace(/-/g, " ").replace(/\b\w/g, (l) => l.toUpperCase()) : "—";
  }

  if (member_since === "—") {
    if (c?.status === "joined") {
      member_since = c?.joinedAt ?? c?.joined_at ?? "—";
      if (member_since === "—") {
        const sinceMatch = str.match(/since\s+(\d{1,2}\/\d{1,2}\/\d{4})/);
        if (sinceMatch) member_since = toISODate(sinceMatch[1]) ?? "—";
      }
    } else if (c?.status === "pending" || c?.status === "canceled") {
      member_since = c?.requestedAt ?? c?.requested_at ?? "—";
    }
  }

  return { display_name: display_name || "—", member_since };
}

function getQueueItemKey(q: any): string {
  return q.id || `${q.group_slug || ''}-${q.finished_at || ''}-${q.status}`;
}

const ISSUE_STATUSES = ['failed', 'error', 'skipped_paid', 'free_trial'];
const ACTIVE_STATUSES = ['processing', 'pending', 'survey_submitted'];
const COMPLETED_STATUSES = ['joined'];

export function AccountsTab({ onFilterLogs }: AccountsTabProps) {
  const [profiles, setProfiles] = useState<any[]>([]);
  const [queueStats, setQueueStats] = useState<Record<string, any>>({});
  const [showAddModal, setShowAddModal] = useState(false);
  const [showImportModal, setShowImportModal] = useState(false);
  const [showCookieModal, setShowCookieModal] = useState<string | null>(null);
  const [cookiePaste, setCookiePaste] = useState('');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [revealCookies, setRevealCookies] = useState<string | null>(null);
  const [csvContent, setCsvContent] = useState('');
  const [loading, setLoading] = useState(false);
  const [fetchingId, setFetchingId] = useState<string | null>(null);
  const [fetchProgress, setFetchProgress] = useState<{ status: string; current: number; total: number; community: string } | null>(null);
  const [cancellingKey, setCancellingKey] = useState<string | null>(null);
  const [leavingKey, setLeavingKey] = useState<string | null>(null);
  const [fetchResultsByProfile, setFetchResultsByProfile] = useState<Record<string, { joined: { slug: string; name: string; joinedAt?: string }[]; pending: { slug: string; name: string; requestedAt: string | null }[]; canceled?: { slug: string; name: string; requestedAt: string | null }[]; fetchedAt: string } | null>>({});
  const [queueByProfile, setQueueByProfile] = useState<Record<string, { group_slug: string; group_name?: string; status: string; error_msg?: string; finished_at?: string; joined_at?: string; id?: string }[]>>({});

  const [showCommunities, setShowCommunities] = useState<Record<string, boolean>>({});
  const [showLiveAttempts, setShowLiveAttempts] = useState<Record<string, boolean>>({});
  const [selectedQueueIds, setSelectedQueueIds] = useState<Record<string, Set<string>>>({});
  const [queueFilter, setQueueFilter] = useState<Record<string, 'all' | 'active' | 'completed' | 'issues'>>({});

  const [newAccount, setNewAccount] = useState({ email: '', password: '', proxy: '', dailyCap: 20, cookies: '' });

  const loadCommunitiesForProfile = useCallback(async (pid: string) => {
    try { const res = await api.getCommunities(pid) ?? await api.getFetchResults(pid); if (res) setFetchResultsByProfile(prev => ({ ...prev, [pid]: res })); } catch (_) {}
  }, []);

  const loadQueueForProfile = useCallback(async (pid: string) => {
    try {
      const [rows, stats] = await Promise.all([api.getQueueForProfile(pid), api.getQueueStats(pid)]);
      const filtered = (rows || []).filter((r: any) => ['failed','error','skipped_paid','survey_submitted','pending','joined','processing'].includes(r.status));
      const statusOrder = (s: string) => {
        if (s === 'processing') return 0;
        if (s === 'pending' || s === 'survey_submitted') return 1;
        if (s === 'joined') return 2;
        return 3;
      };
      const sorted = [...filtered].sort((a, b) => {
        const oa = statusOrder(a.status);
        const ob = statusOrder(b.status);
        if (oa !== ob) return oa - ob;
        const fa = a.finished_at || '';
        const fb = b.finished_at || '';
        return fb.localeCompare(fa);
      });
      setQueueByProfile(prev => ({ ...prev, [pid]: sorted }));
      setQueueStats(prev => ({ ...prev, [pid]: stats || {} }));
    } catch (_) {}
  }, []);

  const fetchProfiles = useCallback(async () => {
    try {
      const data = await api.getProfiles();
      setProfiles(data);
      const stats: Record<string, any> = {};
      for (const p of data) {
        try { stats[p.id] = await api.getQueueStats(p.id); } catch {}
      }
      setQueueStats(stats);
    } catch (err) { console.error('Failed to fetch profiles:', err); }
  }, []);

  useEffect(() => { fetchProfiles(); }, [fetchProfiles]);
  useEffect(() => { profiles.forEach(p => loadCommunitiesForProfile(p.id)); }, [profiles.length, loadCommunitiesForProfile]);

  useEffect(() => {
    const iv = setInterval(fetchProfiles, 5000);
    return () => clearInterval(iv);
  }, [fetchProfiles]);

  useEffect(() => {
    if (!expandedId) return;
    const refresh = () => loadQueueForProfile(expandedId);
    refresh();
    const iv = setInterval(refresh, 5000);
    return () => clearInterval(iv);
  }, [expandedId, loadQueueForProfile]);

  const handleAdd = async () => {
    setLoading(true);
    try {
      await api.createProfile({
        email: newAccount.email,
        password: newAccount.password,
        proxy: newAccount.proxy || undefined,
        daily_cap: newAccount.dailyCap,
        cookie_json: newAccount.cookies || undefined,
      });
      setNewAccount({ email: '', password: '', proxy: '', dailyCap: 20, cookies: '' });
      setShowAddModal(false);
      fetchProfiles();
    } catch (err: any) { alert(err.message); }
    setLoading(false);
  };

  const handleImportCsv = async () => {
    setLoading(true);
    try {
      const result = await api.importCsv(csvContent);
      alert(`Imported: ${result.results.filter((r: any) => r.success).length} success, ${result.results.filter((r: any) => !r.success).length} failed`);
      setCsvContent('');
      setShowImportModal(false);
      fetchProfiles();
    } catch (err: any) { alert(err.message); }
    setLoading(false);
  };

  const handleTestAuth = async (id: string) => {
    try {
      const result = await api.testAuth(id);
      alert(result.valid ? '✓ Auth valid' : `✗ Auth failed: ${result.error}`);
      fetchProfiles();
    } catch (err: any) { alert(err.message); }
  };

  const handleConnect = async (p: any) => {
    const password = prompt('Enter Skool password:', '');
    if (!password) return;
    try {
      await api.connect(p.id, { email: p.email, password });
      fetchProfiles();
    } catch (err: any) { alert(err.message); }
  };

  const handlePasteCookies = async (id: string) => {
    if (!cookiePaste.trim()) return;
    setLoading(true);
    try {
      await api.pasteCookies(id, cookiePaste.trim());
      setShowCookieModal(null);
      setCookiePaste('');
      fetchProfiles();
      alert('✓ Cookies validated and stored!');
    } catch (err: any) { alert(`✗ ${err.message}`); }
    setLoading(false);
  };

  const handleStartStop = async (p: any) => {
    try {
      if (p.is_running) { await api.stopRunner(p.id); }
      else { await api.startRunner(p.id); }
      fetchProfiles();
    } catch (err: any) { alert(err.message); }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this account?')) return;
    await api.deleteProfile(id);
    fetchProfiles();
  };

  const handleUpdateSettings = async (id: string, settings: { join_delay_seconds?: number; max_joins_per_run?: number; daily_cap?: number }) => {
    await api.updateSettings(id, settings);
    fetchProfiles();
  };

  const handleFetch = async (id: string) => {
    setFetchingId(id);
    setFetchProgress({ status: 'Starting...', current: 0, total: 0, community: '' });
    try {
      await api.fetchFromSkool(id);
      const poll = async () => {
        const s = await api.getFetchStatus(id);
        setFetchProgress({ status: s.status, current: s.current, total: s.total, community: s.community || '' });
        if (s.running) setTimeout(poll, 800);
        else {
          fetchProfiles();
          setFetchingId(null);
          setFetchProgress(null);
          try {
            const res = await api.getFetchResults(id);
            if (res) setFetchResultsByProfile(prev => ({ ...prev, [id]: res }));
          } catch (_) {}
          toast.success(`Fetch complete. ${s.resolved || 0} queue items updated. Found ${s.joinedCount || 0} joined, ${s.pendingCount || 0} pending on Skool.`);
        }
      };
      setTimeout(poll, 500);
    } catch (e: any) {
      alert(e.message);
      setFetchingId(null);
      setFetchProgress(null);
    }
  };

  const toggleQueueSelection = (pid: string, key: string, checked: boolean) => {
    setSelectedQueueIds(prev => {
      const set = new Set(prev[pid] || []);
      if (checked) set.add(key);
      else set.delete(key);
      return { ...prev, [pid]: set };
    });
  };

  const selectAllQueue = (pid: string, items: any[]) => {
    const keys = items.map(q => getQueueItemKey(q));
    setSelectedQueueIds(prev => ({ ...prev, [pid]: new Set(keys) }));
  };

  const clearQueueSelection = (pid: string) => {
    setSelectedQueueIds(prev => ({ ...prev, [pid]: new Set() }));
  };

  const handleDeleteSelected = (pid: string) => {
    const ids = Array.from(selectedQueueIds[pid] || []);
    console.log(ids);
  };

  return (
    <TooltipProvider>
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-foreground">Accounts</h2>
            <p className="text-sm text-muted-foreground">Manage connected Skool accounts</p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" className="gap-2" onClick={() => setShowImportModal(true)}>
              <Upload className="h-4 w-4" /> Import CSV
            </Button>
          </div>
        </div>

        <div className="rounded-lg border border-border bg-card overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/50">
                <TableHead>Account</TableHead>
                <TableHead>Proxy</TableHead>
                <TableHead>Auth</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Last Action</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {profiles.length === 0 && (
                <TableRow><TableCell colSpan={6} className="text-center py-8 text-muted-foreground">No accounts yet. Add one to get started.</TableCell></TableRow>
              )}
              {profiles.map((p) => {
                const status = mapAuthStatus(p);
                const stats = queueStats[p.id] || { queued: 0, joined: 0, pending: 0, failed: 0, skipped_paid: 0, survey_submitted: 0 };
                const joiningSlug = (p.current_group_slug || (fetchingId === p.id && fetchProgress?.community ? fetchProgress.community : null));
                return (
                  <>
                    <TableRow key={p.id} className="group">
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <span className="font-medium">{p.email}</span>
                          {p.is_running ? <StatusBadge status="Running" /> : null}
                        </div>
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">{p.proxy || '—'}</TableCell>
                      <TableCell className="text-sm">{p.auth_method || 'none'}</TableCell>
                      <TableCell><StatusBadge status={status} /></TableCell>
                      <TableCell>
                        <button className="text-sm text-primary hover:underline cursor-pointer bg-transparent border-none p-0"
                          onClick={() => onFilterLogs?.(p.email)}>
                          {p.last_action_at ? timeAgo(p.last_action_at) : 'Never'}
                        </button>
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex items-center justify-end gap-1">
                          <Tooltip><TooltipTrigger asChild>
                            <Button variant="ghost" size="sm" className="h-7 text-xs gap-1" onClick={() => handleTestAuth(p.id)}>
                              <TestTube className="h-3 w-3" /> Test Auth
                            </Button>
                          </TooltipTrigger><TooltipContent><p>Tests cookies via GET /self</p></TooltipContent></Tooltip>
                          {status !== 'Connected' && (
                            <>
                              <Button variant="ghost" size="sm" className="h-7 text-xs gap-1" onClick={() => { setShowCookieModal(p.id); setCookiePaste(''); }}>
                                <Cookie className="h-3 w-3" /> Paste Cookies
                              </Button>
                              <Button variant="ghost" size="sm" className="h-7 text-xs gap-1" onClick={() => handleConnect(p)}>
                                <Plug className="h-3 w-3" /> Connect
                              </Button>
                            </>
                          )}
                          <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={() => setExpandedId(expandedId === p.id ? null : p.id)}>
                            {expandedId === p.id ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                    {expandedId === p.id && (
                      <TableRow key={`${p.id}-exp`}>
                        <TableCell colSpan={6} className="bg-muted/30 p-4">
                          <div className="flex flex-col gap-4">
                            <div className="grid grid-cols-2 md:grid-cols-5 gap-4 text-sm">
                            {/* Last Error */}
                            <div>
                              <span className="text-muted-foreground">Last Error</span>
                              <p className="mt-1 font-medium text-destructive">{p.auth_error || p.last_auth_error || 'None'}</p>
                            </div>
                            {/* Queue Stats */}
                            <div>
                              <span className="text-muted-foreground">Queue Stats</span>
                              <p className="mt-1 space-x-2">
                                <span className="text-muted-foreground font-medium">{stats.queued} Queued</span>
                                <span className="text-green-500 font-medium">{stats.joined} Joined</span>
                                <span className="text-yellow-500 font-medium">{stats.pending} Pending</span>
                                <span className="text-red-500 font-medium">{stats.failed} Failed</span>
                                {stats.skipped_paid > 0 && <span className="text-orange-500 font-medium">{stats.skipped_paid} Skipped</span>}
                              </p>
                            </div>
                            {/* Run Controls */}
                            <div>
                              <span className="text-muted-foreground">Run Controls</span>
                              <div className="mt-1 flex flex-col gap-2">
                                <div className="flex items-center gap-2">
                                  {p.is_running ? (
                                    <Button variant="destructive" size="sm" className="h-7 text-xs gap-1" onClick={() => handleStartStop(p)}>
                                      <Square className="h-3 w-3" /> Stop
                                    </Button>
                                  ) : (
                                    <Button size="sm" className="h-7 text-xs gap-1" disabled={status !== 'Connected'} onClick={() => handleStartStop(p)}>
                                      <Play className="h-3 w-3" /> Start
                                    </Button>
                                  )}
                                  <Popover>
                                    <PopoverTrigger asChild>
                                      <Button variant="outline" size="sm" className="h-7 text-xs gap-1">
                                        <Settings2 className="h-3 w-3" /> Delays
                                      </Button>
                                    </PopoverTrigger>
                                    <PopoverContent className="w-56" align="start">
                                      <div className="space-y-3">
                                        <div>
                                          <Label className="text-xs text-muted-foreground">Max joins per day</Label>
                                          <Input type="number" min={1} max={200} defaultValue={p.daily_cap || 20}
                                            onBlur={(e) => handleUpdateSettings(p.id, { daily_cap: Number(e.target.value) })} className="mt-1" />
                                        </div>
                                        <div>
                                          <Label className="text-xs text-muted-foreground">Delay between joins (sec)</Label>
                                          <Input type="number" min={0} defaultValue={p.join_delay_seconds || 30}
                                            onBlur={(e) => handleUpdateSettings(p.id, { join_delay_seconds: Number(e.target.value) })} className="mt-1" />
                                        </div>
                                      </div>
                                    </PopoverContent>
                                  </Popover>
                                </div>
                                <div className="flex flex-col gap-1">
                                  <Tooltip><TooltipTrigger asChild>
                                    <Button variant="outline" size="sm" className="h-7 text-xs gap-1 w-fit" disabled={status !== 'Connected' || !!fetchingId}
                                      onClick={() => handleFetch(p.id)}>
                                      <Download className="h-3 w-3" /> {fetchingId === p.id ? 'Fetching...' : 'Fetch'}
                                    </Button>
                                  </TooltipTrigger><TooltipContent><p>Sync from Skool settings — updates Joined/Pending from skool.com/settings?t=communities</p></TooltipContent></Tooltip>
                                  {fetchingId === p.id && fetchProgress && (
                                    <p className="text-xs text-muted-foreground">
                                      {fetchProgress.status}
                                      {fetchProgress.total > 0 && ` (${fetchProgress.current}/${fetchProgress.total})`}
                                      {fetchProgress.community && ` — ${fetchProgress.community}`}
                                    </p>
                                  )}
                                </div>
                              </div>
                            </div>
                            {/* Countdown + Cookies */}
                            <div className="space-y-3">
                              <div>
                                <span className="text-muted-foreground flex items-center gap-1"><Timer className="h-3 w-3" /> Next Action</span>
                                <p className="mt-1 font-mono font-medium">{formatCountdown(p.next_action_at, p.is_running)}</p>
                                {joiningSlug && (
                                  <p className="mt-0.5 text-xs text-muted-foreground">→ Joining: {joiningSlug}</p>
                                )}
                              </div>
                              <div>
                                <span className="text-muted-foreground">Cookies</span>
                                <div className="mt-1">
                                  <Button variant="ghost" size="sm" className="h-6 text-xs gap-1 p-0"
                                    onClick={() => setRevealCookies(revealCookies === p.id ? null : p.id)}>
                                    {revealCookies === p.id ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                                    {revealCookies === p.id ? 'Hide' : 'Reveal'}
                                  </Button>
                                </div>
                                {revealCookies === p.id && (
                                  <code className="mt-1 block text-xs bg-muted p-2 rounded break-all max-h-24 overflow-auto">{p.cookie_json || 'No cookies'}</code>
                                )}
                              </div>
                            </div>
                          </div>

                          {/* Communities (collapsible) */}
                          {(() => {
                            const data = fetchResultsByProfile[p.id];
                            const canceled = data?.canceled || [];
                            const freeTrials = data?.free_trial || [];
                            const allItems = [...(data?.joined || []).map((c: any) => ({ ...c, status: 'joined' })), ...(data?.pending || []).map((c: any) => ({ ...c, status: 'pending' })), ...canceled.map((c: any) => ({ ...c, status: 'canceled' })), ...freeTrials.map((c: any) => ({ ...c, status: 'free_trial' }))];
                            return (
                              <div className="w-full pt-4 border-t border-border">
                                <button
                                  type="button"
                                  className="flex items-center gap-1 text-muted-foreground text-xs font-medium hover:text-foreground"
                                  onClick={() => setShowCommunities(prev => ({ ...prev, [p.id]: !(prev[p.id] ?? false) }))}
                                >
                                  {(showCommunities[p.id] ?? false) ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                                  Communities {allItems.length > 0 && `(${allItems.length})`}
                                  {allItems.length > 0 && (() => {
                                    const joined = allItems.filter((c: any) => c.status === 'joined').length;
                                    const pending = allItems.filter((c: any) => c.status === 'pending').length;
                                    return <span className="ml-2 text-xs">
                                      {joined > 0 && <span className="text-green-600 font-medium">{joined} 🟢</span>}
                                      {joined > 0 && pending > 0 && ' / '}
                                      {pending > 0 && <span className="text-orange-600 font-medium">{pending} 🟠</span>}
                                    </span>;
                                  })()}
                                </button>
                                {(showCommunities[p.id] ?? false) && allItems.length > 0 && (
                                  <div className="mt-2 rounded border border-border overflow-hidden">
                                    <Table>
                                      <TableHeader>
                                        <TableRow className="bg-muted/50">
                                          <TableHead className="text-xs">Community</TableHead>
                                          <TableHead className="text-xs">Date</TableHead>
                                          <TableHead className="text-xs">Status</TableHead>
                                          <TableHead className="text-xs text-right w-20">Action</TableHead>
                                        </TableRow>
                                      </TableHeader>
                                      <TableBody>
                                        {allItems.map((c: any) => {
                                          const slug = String(c.slug ?? c ?? '').trim();
                                          const cancelKey = `${p.id}:${slug}`;
                                          const isCancelling = cancellingKey === cancelKey;
                                          return (
                                            <TableRow key={slug + '-' + c.status}>
                                              <TableCell className="text-xs min-w-0 break-words whitespace-normal">
  <a
    href={`https://www.skool.com/${slug}`}
    target="_blank"
    rel="noopener noreferrer"
    className="community-link"
  >
    {normalizeCommunityForDisplay(c).display_name}
  </a>
</TableCell>
                                              <TableCell className="text-xs text-muted-foreground">{normalizeCommunityForDisplay(c).member_since}</TableCell>
                                              <TableCell className="text-xs">
                                                {c.status === 'joined' || c.status === 'member' ? (
                                                  <span className="flex items-center gap-1">
                                                    <span className="text-green-600 font-medium">Already inside</span>
                                                    <Button variant="ghost" size="sm" className="h-5 text-[10px] gap-0.5 text-destructive p-1" disabled={leavingKey === cancelKey}
                                                      onClick={async () => { if (!confirm("Leave this group?")) return; if (!slug) { toast.error("Missing community identifier"); return; } setLeavingKey(cancelKey); try { const r = await api.leaveCommunity(p.id, slug); if (r?.ok === true) { await loadCommunitiesForProfile(p.id); fetchProfiles(); toast.success("Left group"); } else { toast.error(r?.error || "Leave failed"); } } catch (e) { toast.error(e?.message || "Leave failed"); } finally { setLeavingKey(null); } }}>
                                                      {leavingKey === cancelKey ? <Loader2 className="h-3 w-3 animate-spin" /> : null} Leave Group
                                                    </Button>
                                                </span>
                                              ) : null}
                                              {c.status === 'pending' && (
                                                <span className="flex items-center gap-1">
                                                  <span className="text-yellow-600 font-medium">Pending</span>
                                                  <Button variant="ghost" size="sm" className="h-5 text-[10px] gap-0.5 text-destructive p-1" disabled={isCancelling}
                                                    onClick={async () => { if (!confirm("Cancel this request?")) return; if (!slug) { toast.error("Missing community identifier"); return; } setCancellingKey(cancelKey); try { const r = await api.cancelCommunity(p.id, slug); if (r?.ok === true) { await loadCommunitiesForProfile(p.id); fetchProfiles(); toast.success("Request cancelled"); } else { toast.error(r?.error || "Cancel failed"); } } catch (e: any) { toast.error(e?.message || "Cancel failed, see logs"); } finally { setCancellingKey(null); } }}>
                                                    {isCancelling ? <Loader2 className="h-3 w-3 animate-spin" /> : <XCircle className="h-3 w-3" />} {isCancelling ? 'Cancelling…' : 'Cancel Request'}
                                                  </Button>
                                                </span>
                                              )}
                                              {c.status === 'canceled' && <span className="text-muted-foreground font-medium">Canceled</span>}
                                              {c.status === 'free_trial' && <span className="text-purple-600 font-medium">Free Trial (Paid)</span>}
                                            </TableCell>
                                            <TableCell className="text-right">
                                              <Button variant="ghost" size="sm" className="h-6 w-6 p-0 text-muted-foreground hover:text-destructive"
                                                onClick={async () => { try { await api.removeCommunity(p.id, slug); await loadCommunitiesForProfile(p.id); toast.success("Removed from list"); } catch (e: any) { toast.error(e.message); } }}>
                                                <Trash2 className="h-3 w-3" />
                                              </Button>
                                            </TableCell>
                                          </TableRow>
                                        );
                                      })}
                                    </TableBody>
                                  </Table>
                                </div>
                                )}
                              </div>
                            );
                          })()}

                          {/* Join Attempts (collapsible) */}
                          <div className="w-full pt-4 border-t border-border">
                            <button
                              type="button"
                              className="flex items-center gap-1 text-muted-foreground text-xs font-medium hover:text-foreground"
                              onClick={() => setShowLiveAttempts(prev => ({ ...prev, [p.id]: !(prev[p.id] ?? true) }))}
                            >
                              {(showLiveAttempts[p.id] ?? true) ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                              Join Attempts (Live)
                            </button>
                            {(showLiveAttempts[p.id] ?? true) && (() => {
                              const queue = queueByProfile[p.id] || [];
                              const filter = queueFilter[p.id] || 'all';
                              const filtered = filter === 'all' ? queue
                                : filter === 'active' ? queue.filter(q => ACTIVE_STATUSES.includes(q.status))
                                : filter === 'completed' ? queue.filter(q => COMPLETED_STATUSES.includes(q.status))
                                : queue.filter(q => ISSUE_STATUSES.includes(q.status));
                              const activeItems = queue.filter(q => ACTIVE_STATUSES.includes(q.status));
                              const completedItems = queue.filter(q => COMPLETED_STATUSES.includes(q.status));
                              const issueItems = queue.filter(q => ISSUE_STATUSES.includes(q.status));
                              const selected = selectedQueueIds[p.id] || new Set();

                              const renderQueueRow = (q: any) => {
                                const key = getQueueItemKey(q);
                                const displayStatus = q.status === 'survey_submitted' ? 'Pending' : (q.status === 'processing' ? 'Processing' : q.status);
                                return (
                                  <TableRow key={key}>
                                    <TableCell className="text-xs w-8">
                                      <input
                                        type="checkbox"
                                        checked={selected.has(key)}
                                        onChange={(e) => toggleQueueSelection(p.id, key, e.target.checked)}
                                        className="rounded"
                                      />
                                    </TableCell>
                                    <TableCell className="text-xs">
                                      <a href={`https://www.skool.com/${q.group_slug || ''}`} target="_blank" rel="noopener noreferrer" className="community-link">
                                        {q.group_name || q.group_slug || '—'}
                                      </a>
                                    </TableCell>
                                    <TableCell className="text-xs">
                                      <span className={q.status === 'joined' ? 'text-green-600 font-medium' : q.status === 'pending' || q.status === 'survey_submitted' ? 'text-orange-600 font-medium' : (q.status === 'failed' || q.status === 'error') ? 'text-red-600 font-medium' : q.status === 'skipped_paid' ? 'text-gray-500 font-medium' : q.status === 'free_trial' ? 'text-purple-600 font-medium' : q.status === 'processing' ? 'text-yellow-600 font-medium' : ''}>
                                        {q.status === 'free_trial' ? 'Free Trial' : displayStatus}
                                      </span>
                                    </TableCell>
                                    <TableCell className="text-xs font-mono text-muted-foreground max-w-[200px] break-words" title={q.error_msg || ''}>{q.error_msg || '—'}</TableCell>
                                    <TableCell className="text-xs text-muted-foreground">{formatDateTime(q.finished_at)}</TableCell>
                                    <TableCell className="text-xs text-muted-foreground">{formatDateTime(q.joined_at)}</TableCell>
                                  </TableRow>
                                );
                              };

                              return (
                                <div className="mt-2 rounded border border-border overflow-hidden bg-muted/50 text-xs">
                                  <div className="p-2 flex flex-wrap items-center gap-2 border-b border-border bg-muted/70">
                                    <select
                                      value={filter}
                                      onChange={(e) => setQueueFilter(prev => ({ ...prev, [p.id]: e.target.value as any }))}
                                      className="text-xs rounded border border-input bg-background px-2 py-1"
                                    >
                                      <option value="all">All</option>
                                      <option value="active">Active ({activeItems.length})</option>
                                      <option value="completed">Completed ({completedItems.length})</option>
                                      <option value="issues">Issues ({issueItems.length})</option>
                                    </select>
                                    <label className="flex items-center gap-1 cursor-pointer">
                                      <input
                                        type="checkbox"
                                        checked={filtered.length > 0 && filtered.every(q => selected.has(getQueueItemKey(q)))}
                                        onChange={(e) => {
                                          if (e.target.checked) selectAllQueue(p.id, filtered);
                                          else clearQueueSelection(p.id);
                                        }}
                                        className="rounded"
                                      />
                                      <span className="text-xs">Select All</span>
                                    </label>
                                    <Button variant="outline" size="sm" className="h-6 text-xs" onClick={() => handleDeleteSelected(p.id)} disabled={selected.size === 0}>
                                      Delete Selected
                                    </Button>
                                    {activeItems.length > 0 && (
                                      <Button variant="outline" size="sm" className="h-6 text-xs text-blue-600 border-blue-200 hover:bg-blue-50" onClick={async () => {
                                        const pendingItems = activeItems.filter(q => q.status === 'pending' || q.status === 'survey_submitted');
                                        if (pendingItems.length === 0) { toast.error('No pending items to move'); return; }
                                        try {
                                          await api.fetchFromSkool(p.id);
                                          // Poll until done
                                          const poll = async (): Promise<void> => {
                                            const s = await api.getFetchStatus(p.id);
                                            if (s.running) { await new Promise(r => setTimeout(r, 800)); return poll(); }
                                            await loadCommunitiesForProfile(p.id);
                                            toast.success(`Synced ${s.joinedCount || 0} joined, ${s.pendingCount || 0} pending to Communities`);
                                          };
                                          await new Promise(r => setTimeout(r, 500));
                                          await poll();
                                        } catch (e: any) { toast.error(e.message); }
                                      }}>
                                        ↑ Sync Pendings to Communities
                                      </Button>
                                    )}
                                  </div>
                                  <div className="max-h-[350px] overflow-y-auto">
                                  {(filter === 'all' || filter === 'active') && activeItems.length > 0 && (
                                    <>
                                      <div className="px-2 py-1 font-medium text-xs text-muted-foreground bg-muted/50">ACTIVE</div>
                                      <Table>
                                        <TableHeader>
                                          <TableRow className="bg-muted/70">
                                            <TableHead className="text-xs w-8"></TableHead>
                                            <TableHead className="text-xs">Community</TableHead>
                                            <TableHead className="text-xs">Status</TableHead>
                                            <TableHead className="text-xs">Error</TableHead>
                                            <TableHead className="text-xs">Finished</TableHead>
                                            <TableHead className="text-xs">Joined</TableHead>
                                          </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                          {activeItems.map((q: any) => renderQueueRow(q))}
                                        </TableBody>
                                      </Table>
                                    </>
                                  )}
                                  {(filter === 'all' || filter === 'completed') && completedItems.length > 0 && (
                                    <>
                                      <div className="px-2 py-1 font-medium text-xs text-muted-foreground bg-muted/50">COMPLETED</div>
                                      <Table>
                                        <TableHeader>
                                          <TableRow className="bg-muted/70">
                                            <TableHead className="text-xs w-8"></TableHead>
                                            <TableHead className="text-xs">Community</TableHead>
                                            <TableHead className="text-xs">Status</TableHead>
                                            <TableHead className="text-xs">Error</TableHead>
                                            <TableHead className="text-xs">Finished</TableHead>
                                            <TableHead className="text-xs">Joined</TableHead>
                                          </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                          {completedItems.map((q: any) => renderQueueRow(q))}
                                        </TableBody>
                                      </Table>
                                    </>
                                  )}
                                  {(filter === 'all' || filter === 'issues') && issueItems.length > 0 && (
                                    <>
                                      <div className="px-2 py-1 font-medium text-xs text-muted-foreground bg-muted/50">ISSUES</div>
                                      <Table>
                                        <TableHeader>
                                          <TableRow className="bg-muted/70">
                                            <TableHead className="text-xs w-8"></TableHead>
                                            <TableHead className="text-xs">Community</TableHead>
                                            <TableHead className="text-xs">Status</TableHead>
                                            <TableHead className="text-xs">Error</TableHead>
                                            <TableHead className="text-xs">Finished</TableHead>
                                            <TableHead className="text-xs">Joined</TableHead>
                                          </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                          {issueItems.map((q: any) => renderQueueRow(q))}
                                        </TableBody>
                                      </Table>
                                    </>
                                  )}
                                  {queue.length === 0 && (
                                    <div className="text-center text-xs text-muted-foreground py-4">No join attempts in this session.</div>
                                  )}
                                  {queue.length > 0 && filtered.length === 0 && (
                                    <div className="text-center text-xs text-muted-foreground py-4">No items match filter.</div>
                                  )}
                                  </div>
                                </div>
                              );
                            })()}
                          </div>
                        </div>
                        </TableCell>
                      </TableRow>
                    )}
                  </>
                );
              })}
            </TableBody>
          </Table>
        </div>

        {/* Add Account Modal */}
        <Dialog open={showAddModal} onOpenChange={setShowAddModal}>
          <DialogContent>
            <DialogHeader><DialogTitle>Add Account</DialogTitle></DialogHeader>
            <div className="space-y-4">
              <div><Label>Email *</Label><Input placeholder="your@email.com" value={newAccount.email} onChange={(e) => setNewAccount({...newAccount, email: e.target.value})} /></div>
              <div><Label>Password *</Label><Input type="password" placeholder="Skool password" value={newAccount.password} onChange={(e) => setNewAccount({...newAccount, password: e.target.value})} /></div>
              <div><Label>Proxy <span className="text-muted-foreground">(optional)</span></Label><Input placeholder="host:port:user:pass" value={newAccount.proxy} onChange={(e) => setNewAccount({...newAccount, proxy: e.target.value})} /></div>
              <div><Label>Daily Cap</Label><Input type="number" value={newAccount.dailyCap} onChange={(e) => setNewAccount({...newAccount, dailyCap: Number(e.target.value)})} /></div>
              <div><Label>Cookies JSON <span className="text-muted-foreground">(optional — auto-connects if empty)</span></Label>
                <Textarea placeholder='[{"name":"auth","value":"..."}]' rows={3} value={newAccount.cookies} onChange={(e) => setNewAccount({...newAccount, cookies: e.target.value})} /></div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setShowAddModal(false)}>Cancel</Button>
              <Button onClick={handleAdd} disabled={loading || !newAccount.email || !newAccount.password}>{loading ? 'Adding...' : 'Add Account'}</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Paste Cookies Modal */}
        <Dialog open={!!showCookieModal} onOpenChange={(open) => { if (!open) setShowCookieModal(null); }}>
          <DialogContent>
            <DialogHeader><DialogTitle>Paste Cookies</DialogTitle></DialogHeader>
            <div className="space-y-4">
              <p className="text-sm text-muted-foreground">
                Log into <a href="https://www.skool.com" target="_blank" className="text-primary underline">skool.com</a> in your browser, then copy cookies using a browser extension (e.g. EditThisCookie, Cookie-Editor).
              </p>
              <p className="text-xs text-muted-foreground">
                Accepts: JSON array from cookie extension, or raw cookie header string (name=val; name2=val2)
              </p>
              <Textarea
                rows={8}
                placeholder={'Paste cookies here...\n\nJSON: [{"name":"auth","value":"...","domain":".skool.com"}]\n\nor raw: cookie_name=value; another=value'}
                value={cookiePaste}
                onChange={(e) => setCookiePaste(e.target.value)}
              />
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setShowCookieModal(null)}>Cancel</Button>
              <Button onClick={() => handlePasteCookies(showCookieModal!)} disabled={loading || !cookiePaste.trim()}>
                {loading ? 'Validating...' : 'Validate & Save'}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Import CSV Modal */}
        <Dialog open={showImportModal} onOpenChange={setShowImportModal}>
          <DialogContent>
            <DialogHeader><DialogTitle>Import Accounts via CSV</DialogTitle></DialogHeader>
            <div className="space-y-4">
              <p className="text-xs text-muted-foreground">Columns: email, password, proxy, daily_cap, cookie_json</p>
              <Textarea rows={8} placeholder="email,password,proxy,daily_cap&#10;john@test.com,pass123,," value={csvContent} onChange={(e) => setCsvContent(e.target.value)} />
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setShowImportModal(false)}>Cancel</Button>
              <Button onClick={handleImportCsv} disabled={loading || !csvContent}>{loading ? 'Importing...' : 'Import'}</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </TooltipProvider>
  );
}
