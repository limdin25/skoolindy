const fs = require('fs');
const apiPath = '/root/.openclaw/workspace/community-join-manager/src/lib/api.ts';
const accountsPath = '/root/.openclaw/workspace/community-join-manager/src/components/AccountsTab.tsx';

// 1. Add leaveGroup to api.ts
let api = fs.readFileSync(apiPath, 'utf8');
if (!api.includes('leaveGroup')) {
  api = api.replace(
    "cancelRequest: (profileId: string, communitySlug: string) => request('/api/communities/cancel-request', { method: 'POST', body: JSON.stringify({ profileId, communitySlug }) }),",
    "cancelRequest: (profileId: string, communitySlug: string) => request('/api/communities/cancel-request', { method: 'POST', body: JSON.stringify({ profileId, communitySlug }) }),\n  leaveGroup: (profileId: string, communitySlug: string) => request('/api/communities/leave', { method: 'POST', body: JSON.stringify({ profileId, communitySlug }) }),"
  );
  fs.writeFileSync(apiPath, api);
  console.log("Added leaveGroup to api.ts");
}

// 2. Add leavingKey state and Leave button to AccountsTab
let accounts = fs.readFileSync(accountsPath, 'utf8');

if (!accounts.includes('leavingKey')) {
  accounts = accounts.replace(
    "const [cancellingKey, setCancellingKey] = useState<string | null>(null);",
    "const [cancellingKey, setCancellingKey] = useState<string | null>(null);\n  const [leavingKey, setLeavingKey] = useState<string | null>(null);"
  );
  console.log("Added leavingKey state");
}

if (!accounts.includes('Leave Group')) {
  // Add Leave button for joined/member - after the "Already inside" span, before pending block
  accounts = accounts.replace(
    `{c.status === 'joined' && <span className="text-green-600 font-medium">Already inside</span>}
                                              {c.status === 'pending' &&`,
    `{c.status === 'joined' || c.status === 'member' ? (
                                                <span className="flex items-center gap-1">
                                                  <span className="text-green-600 font-medium">Already inside</span>
                                                  <Button variant="ghost" size="sm" className="h-5 text-[10px] gap-0.5 text-destructive p-1" disabled={leavingKey === cancelKey}
                                                    onClick={async () => { if (!confirm("Leave this group?")) return; if (!slug) { toast.error("Missing community identifier"); return; } setLeavingKey(cancelKey); try { await api.leaveGroup(p.id, slug); await loadCommunitiesForProfile(p.id); fetchProfiles(); toast.success("Left group"); } catch (e) { toast.error(e?.message || "Leave failed"); } finally { setLeavingKey(null); } }}>
                                                    {leavingKey === cancelKey ? <Loader2 className="h-3 w-3 animate-spin" /> : null} Leave Group
                                                  </Button>
                                                </span>
                                              ) : null}
                                              {c.status === 'pending' &&`
  );
  fs.writeFileSync(accountsPath, accounts);
  console.log("Added Leave Group button");
}

fs.writeFileSync(apiPath, api);
fs.writeFileSync(accountsPath, accounts);
console.log("Frontend patches done");
