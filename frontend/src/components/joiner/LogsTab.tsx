import { useState, useEffect } from "react";
import { RotateCcw, ExternalLink, FileDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "./joiner-api";

interface LogsTabProps {
  filterAccount?: string;
}

export function LogsTab({ filterAccount }: LogsTabProps) {
  const [profiles, setProfiles] = useState<any[]>([]);
  const [selectedAccount, setSelectedAccount] = useState<string>('all');
  const [logs, setLogs] = useState<any[]>([]);
  const [sortBy, setSortBy] = useState<string>('timestamp');
  const [sortOrder, setSortOrder] = useState<string>('desc');

  useEffect(() => {
    api.getProfiles().then(setProfiles).catch(console.error);
  }, []);

  useEffect(() => {
    if (filterAccount) {
      const match = profiles.find(p => p.email === filterAccount);
      if (match) setSelectedAccount(match.id);
    }
  }, [filterAccount, profiles]);

  useEffect(() => {
    const profileId = selectedAccount === 'all' ? undefined : selectedAccount;
    api.getLogs(profileId, 200, sortBy, sortOrder).then(setLogs).catch(console.error);
  }, [selectedAccount, sortBy, sortOrder]);

  const refresh = () => {
    const profileId = selectedAccount === 'all' ? undefined : selectedAccount;
    api.getLogs(profileId, 200, sortBy, sortOrder).then(setLogs).catch(console.error);
  };

  useEffect(() => {
    const iv = setInterval(refresh, 5000);
    return () => clearInterval(iv);
  }, [selectedAccount, sortBy, sortOrder]);

  const getStatusInfo = (log: any): { label: string; className: string } => {
    const msg = (log.message || '').toLowerCase();
    const evt = log.event || '';
    if (msg.includes('confirmed joined') || msg.includes('joined') && log.level !== 'error') return { label: 'joined', className: 'text-green-600 font-medium' };
    if (msg.includes('free trial')) return { label: 'Free Trial', className: 'text-purple-600 font-medium' };
    if (msg.includes('paid community') || msg.includes('skipped_paid')) return { label: 'skipped_paid', className: 'text-gray-500 font-medium' };
    if (msg.includes('pending') || evt === 'join_pending') return { label: 'pending', className: 'text-orange-600 font-medium' };
    if (msg.includes('processing') || evt === 'join_start') return { label: 'processing', className: 'text-yellow-600 font-medium' };
    if (log.level === 'error' || evt === 'join_error') return { label: 'failed', className: 'text-red-600 font-medium' };
    if (evt === 'csv_import') return { label: 'import', className: 'text-blue-600 font-medium' };
    if (evt === 'add_to_account') return { label: 'assigned', className: 'text-blue-600 font-medium' };
    if (evt === 'join_resolved') return { label: 'joined', className: 'text-green-600 font-medium' };
    return { label: evt || '—', className: 'text-muted-foreground' };
  };

  const formatEvent = (log: any) => {
    if (log.event === 'join_result') return log.level === 'error' ? 'Join failed' : 'Joined';
    if (log.event === 'join_error') return 'Join error';
    return log.event || '—';
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Live Logs</h2>
          <p className="text-sm text-muted-foreground">Real-time activity log</p>
        </div>
        <div className="flex gap-2 items-center">
          <Button variant="outline" size="sm" className="gap-2" onClick={async () => {
            try {
              const blob = await api.exportFailedCommunities();
              const a = document.createElement('a');
              a.href = URL.createObjectURL(blob);
              a.download = 'failed-communities-' + Date.now() + '.csv';
              a.click();
            } catch (e) { alert('Export failed'); }
          }}>
            <FileDown className="h-4 w-4" /> Export failed
          </Button>
          <Button variant="outline" size="sm" className="gap-2" onClick={refresh}>
            <RotateCcw className="h-4 w-4" /> Refresh
          </Button>
        </div>
      </div>

      <div className="rounded-lg border border-border bg-card p-4">
        <div className="mb-4 flex flex-wrap gap-4 items-end">
          <div>
            <Label>Filter by Account</Label>
            <Select value={selectedAccount} onValueChange={setSelectedAccount}>
              <SelectTrigger className="mt-1 w-[200px]"><SelectValue placeholder="All accounts" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Accounts</SelectItem>
                {profiles.map(p => <SelectItem key={p.id} value={p.id}>{p.email}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label>Sort by</Label>
            <Select value={sortBy} onValueChange={setSortBy}>
              <SelectTrigger className="mt-1 w-[140px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="timestamp">Timestamp</SelectItem>
                <SelectItem value="event">Event</SelectItem>
                <SelectItem value="community">Community</SelectItem>
                <SelectItem value="message">Message</SelectItem>
                <SelectItem value="account">Account</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label>Order</Label>
            <Select value={sortOrder} onValueChange={setSortOrder}>
              <SelectTrigger className="mt-1 w-[120px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="desc">Newest first</SelectItem>
                <SelectItem value="asc">Oldest first</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        <Table>
          <TableHeader>
            <TableRow className="bg-muted/50">
              <TableHead>Time</TableHead>
              <TableHead>Account</TableHead>
              <TableHead>Community</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Message</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {logs.length === 0 && (
              <TableRow><TableCell colSpan={5} className="text-center py-8 text-muted-foreground">No logs yet</TableCell></TableRow>
            )}
            {logs.map(log => (
              <TableRow key={log.id}>
                <TableCell className="text-xs font-mono whitespace-nowrap">{log.timestamp ? new Date(log.timestamp).toLocaleTimeString() : '—'}</TableCell>
                <TableCell className="text-sm">{log.profile_email || '—'}</TableCell>
                <TableCell>
                  {log.group_slug ? (
                    <a href={`https://www.skool.com/${log.group_slug}`} target="_blank" rel="noopener noreferrer"
                      className="text-primary hover:underline flex items-center gap-1 text-sm">
                      {log.group_slug} <ExternalLink className="h-3 w-3" />
                    </a>
                  ) : '—'}
                </TableCell>
                <TableCell className="text-sm">
                  {(() => { const s = getStatusInfo(log); return <span className={s.className}>{s.label}</span>; })()}
                </TableCell>
                <TableCell className="text-sm text-muted-foreground max-w-xs truncate">{log.message || '—'}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
