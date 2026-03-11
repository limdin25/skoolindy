const BASE = import.meta.env.VITE_JOINER_API || '/api/joiner';

async function request(path: string, opts: RequestInit = {}) {
  const res = await fetch(`${BASE}${path}`, {
    ...opts,
    headers: { 'Content-Type': 'application/json', ...opts.headers },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || res.statusText);
  }
  return res.json();
}

export const api = {
  // Profiles
  getProfiles: () => request('/api/profiles'),
  createProfile: (data: { email: string; password: string; proxy?: string; daily_cap?: number; cookie_json?: string }) =>
    request('/api/profiles', { method: 'POST', body: JSON.stringify(data) }),
  updateProfile: (id: string, data: Record<string, any>) =>
    request(`/api/profiles/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteProfile: (id: string) =>
    request(`/api/profiles/${id}`, { method: 'DELETE' }),
  importCsv: (csv_content: string) =>
    request('/api/profiles/import-csv', { method: 'POST', body: JSON.stringify({ csv_content }) }),

  // Auth
  testAuth: (id: string) => request(`/api/profiles/${id}/skool-auth`),
  connect: (id: string, data?: { email?: string; password?: string }) =>
    request(`/api/profiles/${id}/connect`, { method: 'POST', body: JSON.stringify(data || {}) }),
  pasteCookies: (id: string, cookies: string) =>
    request(`/api/profiles/${id}/paste-cookies`, { method: 'POST', body: JSON.stringify({ cookies }) }),

  // Runner
  startRunner: (id: string) => request(`/api/profiles/${id}/run`, { method: 'POST' }),
  stopRunner: (id: string) => request(`/api/profiles/${id}/stop`, { method: 'POST' }),
  updateSettings: (id: string, data: { join_delay_seconds?: number; max_joins_per_run?: number }) =>
    request(`/api/profiles/${id}/settings`, { method: 'PATCH', body: JSON.stringify(data) }),
  getQueueStats: (id: string) => request(`/api/profiles/${id}/queue-stats`),

  // Queue
  getQueue: (profileId?: string, sortBy?: string, order?: string) =>
    request(`/api/queue?profile_id=${profileId || 'all'}&sort_by=${sortBy || 'created_at'}&order=${order || 'asc'}`),
  addToQueue: (data: { profile_id: string; group_slug: string; group_name?: string }) =>
    request('/api/queue', { method: 'POST', body: JSON.stringify(data) }),
  addToAccount: (target_profile_id: string, ids: string[]) =>
    request('/api/queue/add-to-account', { method: 'POST', body: JSON.stringify({ target_profile_id, ids }) }),
  deleteQueue: (opts: { ids?: string[]; all?: boolean; profile_id?: string }) =>
    request('/api/queue/delete', { method: 'POST', body: JSON.stringify(opts) }),
  importQueueCsv: (profile_id: string | null, csv_content: string) =>
    request('/api/queue/import-csv', { method: 'POST', body: JSON.stringify({ profile_id, csv_content }) }),

  // Logs
  fetchFromSkool: (profileId: string) => request(`/api/communities/fetch/${profileId}`, { method: 'POST' }),
  getFetchStatus: (profileId: string) => request(`/api/communities/fetch/${profileId}/status`),
  getFetchResults: (profileId: string) => request(`/api/communities/fetch/${profileId}/results`),
  getCommunities: (profileId: string) => request(`/api/communities/fetch/${profileId}/results`),
  fetchAllFromSkool: () => request('/api/communities/fetch-all', { method: 'POST' }),
  getFetchAllStatus: () => request('/api/communities/fetch-all/status'),
  removeCommunity: (profileId: string, slug: string) =>
    request(`/api/communities/remove/${profileId}/${encodeURIComponent(slug)}`, { method: 'DELETE' }),
  cancelCommunity: (profileId: string, slug: string) =>
    request('/api/communities/cancel-request', { method: 'POST', body: JSON.stringify({ profileId, slug }) }),
  leaveCommunity: (profileId: string, slug: string) =>
    request('/api/communities/leave', { method: 'POST', body: JSON.stringify({ profileId, slug }) }),
  getQueueForProfile: (profileId: string) =>
    request(`/api/queue?profile_id=${profileId}&limit=100000`),
  exportFailedCommunities: () => fetch(BASE + '/api/logs/export?status=failed&format=csv').then(r => r.blob()),
  getLogs: (profileId?: string, limit = 200, sortBy?: string, order?: string) =>
    request(`/api/logs?profile_id=${profileId || 'all'}&limit=${limit}&sort_by=${sortBy || 'timestamp'}&order=${order || 'desc'}`),

  // Survey/Discovery
  getDiscoveryInfo: (id: string) => request(`/api/profiles/${id}/discovery-info`),
  updateDiscoveryInfo: (id: string, data: Record<string, any>) =>
    request(`/api/profiles/${id}/discovery-info`, { method: 'PUT', body: JSON.stringify(data) }),

  // Settings
  getSettings: () => request('/api/settings'),
  updateSettings2: (data: Record<string, any>) =>
    request('/api/settings', { method: 'POST', body: JSON.stringify(data) }),
};
