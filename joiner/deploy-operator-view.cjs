#!/usr/bin/env node
/**
 * Upgrade Communities panel to Operator View: Join Attempts (Live) + 5s polling.
 * Run on server: cd /root/.openclaw/workspace/community-join-manager && node deploy-operator-view.cjs
 */
const fs = require('fs');
const path = require('path');

const ROOT = '/root/.openclaw/workspace/community-join-manager';
const ACCOUNTS = path.join(ROOT, 'src/components/AccountsTab.tsx');
const API = path.join(ROOT, 'src/lib/api.ts');

function patch(name, fn) {
  try {
    fn();
    console.log('OK:', name);
  } catch (e) {
    console.error('FAIL:', name, e.message);
  }
}

patch('api: add getQueueForProfile', () => {
  let s = fs.readFileSync(API, 'utf8');
  if (s.includes('getQueueForProfile')) return;
  s = s.replace(
    'getQueue: (profileId?: string, sortBy?: string, order?: string) =>\n    request(`/api/queue?profile_id=${profileId || \'all\'}&sort_by=${sortBy || \'created_at\'}&order=${order || \'asc\'}`),',
    'getQueue: (profileId?: string, sortBy?: string, order?: string) =>\n    request(`/api/queue?profile_id=${profileId || \'all\'}&sort_by=${sortBy || \'created_at\'}&order=${order || \'asc\'}`),\n  getQueueForProfile: (profileId: string) =>\n    request(`/api/queue?profile_id=${profileId}&limit=200&sort_by=finished_at&order=desc`),'
  );
  fs.writeFileSync(API, s);
});

patch('AccountsTab: add queueByProfile state', () => {
  let s = fs.readFileSync(ACCOUNTS, 'utf8');
  if (s.includes('queueByProfile')) return;
  s = s.replace(
    "const [fetchResultsByProfile, setFetchResultsByProfile] = useState<Record<string, { joined: { slug: string; name: string; joinedAt?: string }[]; pending: { slug: string; name: string; requestedAt: string | null }[]; canceled?: { slug: string; name: string; requestedAt: string | null }[]; fetchedAt: string } | null>>({});",
    "const [fetchResultsByProfile, setFetchResultsByProfile] = useState<Record<string, { joined: { slug: string; name: string; joinedAt?: string }[]; pending: { slug: string; name: string; requestedAt: string | null }[]; canceled?: { slug: string; name: string; requestedAt: string | null }[]; fetchedAt: string } | null>>({});\n  const [queueByProfile, setQueueByProfile] = useState<Record<string, { group_slug: string; group_name?: string; status: string; error_msg?: string; finished_at?: string; joined_at?: string }[]>>({});"
  );
  fs.writeFileSync(ACCOUNTS, s);
});

patch('AccountsTab: add loadQueueForProfile and polling useEffect', () => {
  let s = fs.readFileSync(ACCOUNTS, 'utf8');
  if (s.includes('loadQueueForProfile')) return;
  s = s.replace(
    "const loadCommunitiesForProfile = useCallback(async (pid: string) => {\n    try { const res = await api.getCommunities(pid) ?? await api.getFetchResults(pid); if (res) setFetchResultsByProfile(prev => ({ ...prev, [pid]: res })); } catch (_) {}\n  }, []);",
    `const loadCommunitiesForProfile = useCallback(async (pid: string) => {
    try { const res = await api.getCommunities(pid) ?? await api.getFetchResults(pid); if (res) setFetchResultsByProfile(prev => ({ ...prev, [pid]: res })); } catch (_) {}
  }, []);
  const loadQueueForProfile = useCallback(async (pid: string) => {
    try {
      const [rows, stats] = await Promise.all([api.getQueueForProfile(pid), api.getQueueStats(pid)]);
      const filtered = (rows || []).filter((r: any) => ['failed','error','skipped_paid','survey_submitted','pending','joined','processing'].includes(r.status));
      setQueueByProfile(prev => ({ ...prev, [pid]: filtered }));
      setQueueStats(prev => ({ ...prev, [pid]: stats || {} }));
    } catch (_) {}
  }, []);`
  );
  fs.writeFileSync(ACCOUNTS, s);
});

patch('AccountsTab: polling useEffect for Join Attempts (5 seconds)', () => {
  let s = fs.readFileSync(ACCOUNTS, 'utf8');
  if (s.includes('Join Attempts poll')) return;
  s = s.replace(
    "// Auto-refresh every 5 seconds for countdown updates\n  useEffect(() => {\n    const iv = setInterval(fetchProfiles, 5000);\n    return () => clearInterval(iv);\n  }, [fetchProfiles]);",
    `// Auto-refresh every 5 seconds for countdown updates
  useEffect(() => {
    const iv = setInterval(fetchProfiles, 5000);
    return () => clearInterval(iv);
  }, [fetchProfiles]);

  // Join Attempts poll (5 seconds) — only when a profile is expanded, only refresh queue (not Skool membership)
  useEffect(() => {
    if (!expandedId) return;
    const refresh = () => loadQueueForProfile(expandedId);
    refresh();
    const iv = setInterval(refresh, 5000);
    return () => clearInterval(iv);
  }, [expandedId, loadQueueForProfile]);`
  );
  fs.writeFileSync(ACCOUNTS, s);
});

patch('AccountsTab: add Join Attempts section below Communities', () => {
  let s = fs.readFileSync(ACCOUNTS, 'utf8');
  if (s.includes('Join Attempts (Live)')) return;
  const joinAttemptsBlock = `
                          {/* Join Attempts (Live) */}
                          <div className="w-full pt-4 border-t border-border">
                            <span className="text-muted-foreground text-xs font-medium block mb-2">Join Attempts (Live)</span>
                            <div className="rounded border border-border overflow-hidden bg-muted/50">
                              <Table>
                                <TableHeader>
                                  <TableRow className="bg-muted/70">
                                    <TableHead className="text-xs">Community</TableHead>
                                    <TableHead className="text-xs">Status</TableHead>
                                    <TableHead className="text-xs">Error</TableHead>
                                    <TableHead className="text-xs">Finished</TableHead>
                                    <TableHead className="text-xs">Joined</TableHead>
                                  </TableRow>
                                </TableHeader>
                                <TableBody>
                                  {((queueByProfile[p.id] || [])).map((q: any) => (
                                    <TableRow key={q.id || q.group_slug + q.finished_at}>
                                      <TableCell className="text-xs">
                                        <a href={\`https://www.skool.com/\${q.group_slug || ''}\`} target="_blank" rel="noopener noreferrer" className="community-link">
                                          {q.group_name || q.group_slug || '—'}
                                        </a>
                                      </TableCell>
                                      <TableCell className="text-xs">
                                        <span className={q.status === 'joined' ? 'text-green-600 font-medium' : q.status === 'pending' ? 'text-orange-600 font-medium' : (q.status === 'failed' || q.status === 'error') ? 'text-red-600 font-medium' : q.status === 'skipped_paid' ? 'text-gray-500 font-medium' : q.status === 'survey_submitted' ? 'text-blue-600 font-medium' : q.status === 'processing' ? 'text-yellow-600 font-medium' : ''}>
                                          {q.status === 'processing' ? 'Processing' : q.status}
                                        </span>
                                      </TableCell>
                                      <TableCell className="text-xs font-mono text-muted-foreground max-w-[200px] truncate" title={q.error_msg || ''}>{q.error_msg || '—'}</TableCell>
                                      <TableCell className="text-xs text-muted-foreground">{q.finished_at ? q.finished_at.slice(0, 19).replace('T', ' ') : '—'}</TableCell>
                                      <TableCell className="text-xs text-muted-foreground">{q.joined_at ? q.joined_at.slice(0, 19).replace('T', ' ') : '—'}</TableCell>
                                    </TableRow>
                                  ))}
                                  {(!queueByProfile[p.id] || queueByProfile[p.id].length === 0) && (
                                    <TableRow><TableCell colSpan={5} className="text-center text-xs text-muted-foreground py-4">No join attempts in this session.</TableCell></TableRow>
                                  )}
                                </TableBody>
                              </Table>
                            </div>
                          </div>`;
  s = s.replace(
    `})()}
                        </div>
                        </TableCell>
                      </TableRow>
                    )}
                  </>
                );
              })}
            </TableBody>`,
    `})()}
${joinAttemptsBlock}
                        </div>
                        </TableCell>
                      </TableRow>
                    )}
                  </>
                );
              })}
            </TableBody>`
  );
  fs.writeFileSync(ACCOUNTS, s);
});

console.log('Deploy Operator View complete.');
