const BASE = import.meta.env.VITE_API_URL || 'http://38.242.229.161:3114';

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
  getQueue: (profileId?: string) => request(`/api/queue${profileId ? `?profile_id=${profileId}` : ''}`),
  addToQueue: (data: { profile_id: string; group_slug: string; group_name?: string }) =>
    request('/api/queue', { method: 'POST', body: JSON.stringify(data) }),

  importQueueCsv: (profile_id: string, csv_content: string) =>
    request('/api/queue/import-csv', { method: 'POST', body: JSON.stringify({ profile_id, csv_content }) }),
  reorderQueue: (profile_id: string, ordered_ids: string[]) =>
    request('/api/queue/reorder', { method: 'PATCH', body: JSON.stringify({ profile_id, ordered_ids }) }),

  // Pool (unassigned communities)
  getPool: () => request('/api/pool'),
  importPoolCsv: (csv_content: string) =>
    request('/api/pool/import-csv', { method: 'POST', body: JSON.stringify({ csv_content }) }),
  assignPool: (profile_id: string, ids: string[]) =>
    request('/api/pool/assign', { method: 'POST', body: JSON.stringify({ profile_id, ids }) }),

  // Logs
  fetchFromSkool: (profileId: string) => request(`/api/communities/fetch/${profileId}`, { method: 'POST' }),
  fetchAllFromSkool: () => request('/api/communities/fetch-all', { method: 'POST' }),
  getFetchAllStatus: () => request('/api/communities/fetch-all/status'),
  exportFailedCommunities: () => fetch(BASE + '/api/logs/export?status=failed&format=csv').then(r => r.blob()),
  getLogs: (profileId?: string, limit = 200) =>
    request(`/api/logs?profile_id=${profileId || 'all'}&limit=${limit}`),

  // Survey/Discovery
  getDiscoveryInfo: (id: string) => request(`/api/profiles/${id}/discovery-info`),
  updateDiscoveryInfo: (id: string, data: Record<string, any>) =>
    request(`/api/profiles/${id}/discovery-info`, { method: 'PUT', body: JSON.stringify(data) }),

  // Settings
  getSettings: () => request('/api/settings'),
  updateSettings2: (data: Record<string, any>) =>
    request('/api/settings', { method: 'POST', body: JSON.stringify(data) }),
};
