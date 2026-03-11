const fs = require('fs');
const pathMod = require('path');
const p = pathMod.join(__dirname, 'src/lib/api.ts');
let s = fs.readFileSync(p, 'utf8');
const bad = `  importQueueCsv: (profile_id: string, csv_content: string) =>
    request('/api/queue/import-csv',
  importUnassignedCsv: (items) => request('/api/unassigned/batch', { method: 'POST', body: JSON.stringify({ items }) }), { method: 'POST', body: JSON.stringify({ profile_id, csv_content }) }),`;
const good = `  importQueueCsv: (profile_id: string, csv_content: string) =>
    request('/api/queue/import-csv', { method: 'POST', body: JSON.stringify({ profile_id, csv_content }) }),
  importUnassignedCsv: (items) => request('/api/unassigned/batch', { method: 'POST', body: JSON.stringify({ items }) }),`;
if (s.includes(bad)) {
  s = s.replace(bad, good);
  fs.writeFileSync(p, s);
  console.log('Fixed api.ts');
} else {
  console.log('Pattern not found, checking...');
  console.log(s.slice(s.indexOf('importQueueCsv') - 5, s.indexOf('// Logs')));
}
