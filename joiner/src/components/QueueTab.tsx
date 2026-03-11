import { useState, useEffect } from "react";
import { Upload, ExternalLink, User, Download, ChevronUp, ChevronDown, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api";

export function QueueTab() {
  const [profiles, setProfiles] = useState<any[]>([]);
  const [accountFilter, setAccountFilter] = useState<string>('all');
  const [queue, setQueue] = useState<any[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [addToAccountId, setAddToAccountId] = useState<string>('');
  const [showAddToModal, setShowAddToModal] = useState(false);
  const [showCsvModal, setShowCsvModal] = useState(false);
  const [csvContent, setCsvContent] = useState('');
  const [importToAccount, setImportToAccount] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [fetchAllState, setFetchAllState] = useState({ running: false, done: 0, total: 0, current: null, resolved: 0 });
  const [sortBy, setSortBy] = useState<string>('created_at');
  const [sortOrder, setSortOrder] = useState<string>('asc');
  const [checkCount, setCheckCount] = useState<string>('all');

  useEffect(() => {
    api.getProfiles().then(setProfiles).catch(console.error);
  }, []);

  useEffect(() => {
    api.getQueue(accountFilter === 'all' ? undefined : accountFilter, sortBy, sortOrder).then(setQueue).catch(console.error);
  }, [accountFilter, sortBy, sortOrder]);

  useEffect(() => {
    if (!fetchAllState.running) return;
    const iv = setInterval(async () => {
      const s = await api.getFetchAllStatus();
      setFetchAllState(s);
      if (!s.running) refresh();
    }, 1500);
    return () => clearInterval(iv);
  }, [fetchAllState.running]);

  const refresh = () => {
    api.getQueue(accountFilter === 'all' ? undefined : accountFilter, sortBy, sortOrder).then(setQueue).catch(console.error);
  };

  const handleCheckAll = (checked: boolean) => {
    if (!checked) { setSelectedIds(new Set()); return; }
    const n = checkCount === 'all' ? queue.length : Math.min(parseInt(checkCount) || queue.length, queue.length);
    setSelectedIds(new Set(queue.slice(0, n).map(q => q.id)));
  };

  const statusMap: Record<string, string> = {
    queued: 'Queued', processing: 'Processing', joined: 'Joined', pending: 'Pending',
    survey_submitted: 'Survey Submitted', skipped_paid: 'Skipped Paid', failed: 'Failed', error: 'Failed'
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Communities & Queue</h2>
          <p className="text-sm text-muted-foreground">All communities across accounts — select and add to any account</p>
        </div>
      </div>

      <div className="rounded-lg border-2 border-primary/20 bg-primary/5 p-4">
        <div className="flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-2 text-sm font-medium">
            <User className="h-4 w-4" />
            Filter:
          </div>
          <Select value={accountFilter} onValueChange={(v) => { setAccountFilter(v); setSelectedIds(new Set()); }}>
            <SelectTrigger className="w-[220px] bg-white">
              <SelectValue placeholder="All accounts" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All accounts</SelectItem>
              <SelectItem value="__UNASSIGNED__">Unassigned</SelectItem>
              {profiles.map(p => (
                <SelectItem key={p.id} value={p.id}>{p.email}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <div className="flex gap-2 ml-auto">
            <Button variant="outline" size="sm" className="gap-2" disabled={fetchAllState.running} onClick={async () => {
              try {
                if (accountFilter === 'all') { alert('Select an account to fetch for'); return; }
                await api.fetchFromSkool(accountFilter);
                refresh();
                alert('Fetched — check queue');
              } catch (e: any) { alert(e.message); }
            }}>
              <Download className="h-4 w-4" /> Fetch from Skool
            </Button>
            <Button variant="outline" size="sm" className="gap-2" disabled={fetchAllState.running} onClick={async () => {
              try {
                await api.fetchAllFromSkool();
                setFetchAllState({ running: true, done: 0, total: 1, current: null, resolved: 0 });
              } catch (e: any) { alert(e.message); }
            }}>
              {fetchAllState.running ? `${fetchAllState.done}/${fetchAllState.total} profiles` : 'Fetch All'}
            </Button>
            <Button variant="outline" size="sm" className="gap-2" disabled={selectedIds.size === 0} onClick={() => setShowAddToModal(true)}>
              Add to account {selectedIds.size > 0 ? `(${selectedIds.size})` : ''}
            </Button>
            <Button variant="outline" size="sm" className="gap-2" onClick={() => { setImportToAccount('__UNASSIGNED__'); setCsvContent(''); setShowCsvModal(true); }}>
              <Upload className="h-4 w-4" /> Import CSV
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" className="gap-2" disabled={queue.length === 0 || loading}>
                  <Trash2 className="h-4 w-4" /> Delete
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem disabled={selectedIds.size === 0} onClick={async () => {
                  if (selectedIds.size === 0) return;
                  if (!confirm(`Delete ${selectedIds.size} selected communities?`)) return;
                  setLoading(true);
                  try {
                    const r = await api.deleteQueue({ ids: Array.from(selectedIds) });
                    alert(`Deleted ${r.deleted} communities`);
                    setSelectedIds(new Set()); refresh();
                  } catch (e: any) { alert(e.message); }
                  setLoading(false);
                }}>
                  Delete selected {selectedIds.size > 0 ? `(${selectedIds.size})` : ''}
                </DropdownMenuItem>
                <DropdownMenuItem onClick={async () => {
                  if (!confirm(`Delete ALL ${queue.length} communities? This cannot be undone.`)) return;
                  setLoading(true);
                  try {
                    const r = await api.deleteQueue({ all: true, profile_id: accountFilter !== 'all' ? accountFilter : undefined });
                    alert(`Deleted ${r.deleted} communities`);
                    setSelectedIds(new Set()); refresh();
                  } catch (e: any) { alert(e.message); }
                  setLoading(false);
                }}>
                  Delete all ({queue.length})
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </div>

      <div className="rounded-lg border border-border bg-card overflow-hidden">
        <div className="px-4 py-2 bg-muted/50 border-b flex items-center justify-between">
          <span className="text-sm font-medium">{queue.length} communities</span>
          <span className="text-xs text-muted-foreground">
            {queue.filter(q => q.status === 'joined').length} joined · {queue.filter(q => q.status === 'queued').length} queued · {queue.filter(q => q.status === 'failed' || q.status === 'error').length} failed
          </span>
        </div>
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/50">
              <TableHead className="w-32">
                <div className="flex items-center gap-1">
                  <input type="checkbox" checked={queue.length > 0 && (() => {
                    const n = checkCount === 'all' ? queue.length : Math.min(parseInt(checkCount) || 0, queue.length);
                    if (n === 0) return false;
                    const firstN = new Set(queue.slice(0, n).map(q => q.id));
                    return selectedIds.size === n && [...firstN].every(id => selectedIds.has(id));
                  })()}
                    onChange={(e) => handleCheckAll(e.target.checked)} />
                  <Select value={checkCount} onValueChange={setCheckCount}>
                    <SelectTrigger className="h-7 w-[90px] ml-1 border-0 bg-transparent shadow-none"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">Check all</SelectItem>
                      <SelectItem value="10">Check 10</SelectItem>
                      <SelectItem value="25">Check 25</SelectItem>
                      <SelectItem value="50">Check 50</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </TableHead>
              <TableHead>
                <button className="flex items-center gap-1 hover:underline" onClick={() => { const same = sortBy === 'community'; setSortBy('community'); setSortOrder(same && sortOrder === 'asc' ? 'desc' : 'asc'); }}>
                  Community {sortBy === 'community' ? (sortOrder === 'asc' ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />) : null}
                </button>
              </TableHead>
              <TableHead>
                <button className="flex items-center gap-1 hover:underline" onClick={() => { const same = sortBy === 'account'; setSortBy('account'); setSortOrder(same && sortOrder === 'asc' ? 'desc' : 'asc'); }}>
                  Account {sortBy === 'account' ? (sortOrder === 'asc' ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />) : null}
                </button>
              </TableHead>
              <TableHead>
                <button className="flex items-center gap-1 hover:underline" onClick={() => { const same = sortBy === 'status'; setSortBy('status'); setSortOrder(same && sortOrder === 'asc' ? 'desc' : 'asc'); }}>
                  Status {sortBy === 'status' ? (sortOrder === 'asc' ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />) : null}
                </button>
              </TableHead>
              <TableHead>
                <button type="button" className="flex items-center gap-1 hover:underline" onClick={() => { const same = sortBy === 'error'; setSortBy('error'); setSortOrder(same && sortOrder === 'asc' ? 'desc' : 'asc'); }}>
                  Error {sortBy === 'error' ? (sortOrder === 'asc' ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />) : null}
                </button>
              </TableHead>
              <TableHead>
                <button type="button" className="flex items-center gap-1 hover:underline" onClick={() => { const same = sortBy === 'created_at'; setSortBy('created_at'); setSortOrder(same && sortOrder === 'asc' ? 'desc' : 'asc'); }}>
                  Queued {sortBy === 'created_at' ? (sortOrder === 'asc' ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />) : null}
                </button>
              </TableHead>
              <TableHead>
                <button type="button" className="flex items-center gap-1 hover:underline" onClick={() => { const same = sortBy === 'finished_at'; setSortBy('finished_at'); setSortOrder(same && sortOrder === 'asc' ? 'desc' : 'asc'); }}>
                  Finished {sortBy === 'finished_at' ? (sortOrder === 'asc' ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />) : null}
                </button>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {queue.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} className="text-center py-8 text-muted-foreground">
                  No communities. Use Import CSV to add.
                </TableCell>
              </TableRow>
            )}
            {queue.map(item => (
              <TableRow key={item.id}>
                <TableCell>
                  <input type="checkbox" checked={selectedIds.has(item.id)}
                    onChange={(e) => {
                      setSelectedIds(prev => {
                        const n = new Set(prev);
                        if (e.target.checked) n.add(item.id); else n.delete(item.id);
                        return n;
                      });
                    }} />
                </TableCell>
                <TableCell>
                  <a href={`https://www.skool.com/${item.group_slug}`} target="_blank" rel="noopener noreferrer"
                    className="text-primary hover:underline flex items-center gap-1">
                    {item.group_name || item.group_slug} <ExternalLink className="h-3 w-3" />
                  </a>
                </TableCell>
                <TableCell className="text-sm">{item.profile_email || <span className="text-muted-foreground italic">Unassigned</span>}</TableCell>
                <TableCell><StatusBadge status={statusMap[item.status] || item.status} /></TableCell>
                <TableCell className="text-sm text-muted-foreground max-w-[180px] truncate">{item.error_msg || '—'}</TableCell>
                <TableCell className="text-sm">{item.created_at ? new Date(item.created_at).toLocaleTimeString() : '—'}</TableCell>
                <TableCell className="text-sm">{item.finished_at ? new Date(item.finished_at).toLocaleTimeString() : '—'}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Add to account modal */}
      <Dialog open={showAddToModal} onOpenChange={setShowAddToModal}>
        <DialogContent>
          <DialogHeader><DialogTitle>Add to account</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <p className="text-sm">Add {selectedIds.size} selected communities to:</p>
            <Select value={addToAccountId} onValueChange={setAddToAccountId}>
              <SelectTrigger><SelectValue placeholder="Choose account..." /></SelectTrigger>
              <SelectContent>
                {profiles.map(p => (
                  <SelectItem key={p.id} value={p.id}>{p.email}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowAddToModal(false)}>Cancel</Button>
            <Button disabled={!addToAccountId || loading} onClick={async () => {
              setLoading(true);
              try {
                const r = await api.addToAccount(addToAccountId, Array.from(selectedIds));
                alert(`✅ ${r.added} added${r.skipped ? `, ${r.skipped} already in queue` : ''}`);
                setSelectedIds(new Set()); setShowAddToModal(false); refresh();
              } catch (e: any) { alert(e.message); }
              setLoading(false);
            }}>{loading ? 'Adding...' : 'Add'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Import CSV modal */}
      <Dialog open={showCsvModal} onOpenChange={setShowCsvModal}>
        <DialogContent>
          <DialogHeader><DialogTitle>Import CSV</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">Communities will be imported as unassigned. You can assign them to an account later using "Add to account".</p>
            <div>
              <Label>Upload CSV or paste content</Label>
              <div className="flex gap-2 mt-1 mb-2">
                <input type="file" accept=".csv" className="hidden" id="csv-upload"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) { const r = new FileReader(); r.onload = () => setCsvContent(String(r.result)); r.readAsText(f); }
                    e.target.value = '';
                  }} />
                <Button type="button" variant="outline" size="sm" onClick={() => document.getElementById('csv-upload')?.click()}>
                  <Upload className="h-4 w-4 mr-2" /> Choose file
                </Button>
              </div>
              <Textarea rows={8} placeholder="Name,Members,Online,Private,Pricing,Price,URL,Bio,Description" value={csvContent} onChange={e => setCsvContent(e.target.value)} className="font-mono text-xs" />
              <p className="text-xs text-muted-foreground mt-1">Expected: Name, Members, URL. Paid auto-skipped. Duplicates skipped.</p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowCsvModal(false)}>Cancel</Button>
            <Button disabled={loading || !csvContent} onClick={async () => {
              setLoading(true);
              try {
                const result = await api.importQueueCsv(null, csvContent);
                alert(`✅ ${result.added} added, ${result.skipped} skipped`);
                setCsvContent(''); setShowCsvModal(false); refresh();
              } catch (err: any) { alert(err.message); }
              setLoading(false);
            }}>{loading ? 'Importing...' : 'Import'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
