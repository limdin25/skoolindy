#!/usr/bin/env node
/** Remove paid-skip logic from CSV import - only skip duplicates. Run on server. */
const fs = require('fs');
const path = require('path');
const ROOT = process.env.COMMUNITY_JOIN_ROOT || '/root/.openclaw/workspace/community-join-manager';
const SERVER = path.join(ROOT, 'backend/server.js');

let s = fs.readFileSync(SERVER, 'utf8');

const from = `      const pricing = col(row, 'Pricing', 'pricing');
      const priceStr = col(row, 'Price', 'price');
      // Extract slug from URL like https://www.skool.com/ai-automation-society
      let slug = '';
      if (url) { const match = url.match(/skool\\.com\\/([^\\/\\?#]+)/); if (match) slug = match[1]; }
      if (!slug && name) slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
      if (!slug) { results.push({ name, success: false, error: 'No URL or Name' }); continue; }

      // Skip paid communities (Pricing=Paid or Price has numeric value > 0)
      if (pricing && pricing.toLowerCase() === 'paid') {
        results.push({ name, slug, success: false, error: 'Paid community — skipped' });
        continue;
      }
      const priceNum = parseFloat(String(priceStr || '').replace(/[^0-9.]/g, ''));
      if (!isNaN(priceNum) && priceNum > 0) {
        results.push({ name, slug, success: false, error: 'Paid community — skipped' });
        continue;
      }

      const id = uuidv4();`;

const to = `      // Extract slug from URL like https://www.skool.com/ai-automation-society
      let slug = '';
      if (url) { const match = url.match(/skool\\.com\\/([^\\/\\?#]+)/); if (match) slug = match[1]; }
      if (!slug && name) slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
      if (!slug) { results.push({ name, success: false, error: 'No URL or Name' }); continue; }

      const id = uuidv4();`;

if (s.includes('Skip paid communities')) {
  s = s.replace(from, to);
  fs.writeFileSync(SERVER, s);
  console.log('OK: removed paid-skip logic from import-csv');
} else {
  console.log('Paid-skip logic already removed or not found.');
}
