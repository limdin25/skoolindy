const fs = require('fs');
const path = '/root/.openclaw/workspace/community-join-manager/backend/joinCommunity.js';
let s = fs.readFileSync(path, 'utf8');

// Fix cancelRequestOnSkool: use slugifyName for matching modal text to slug
const oldMatch = `      const bodyText = (await page.textContent('body')).toLowerCase();
      const nameFromSlug = communitySlug.replace(/-/g, ' ');
      if (/cancel request/i.test(bodyText) && (bodyText.includes(communitySlug) || bodyText.includes(nameFromSlug))) {`;

const newMatch = `      const bodyText = await page.textContent('body');
      const bodyLower = (bodyText || '').toLowerCase();
      const requestedMatch = bodyText?.match(/you requested membership to ([^.©]+?)\s*[©]?\s*on\s+/i);
      const titleMatch = bodyText?.match(/([A-Z0-9][A-Za-z0-9\s]+(?:©)?)\s*Membership settings/i);
      const modalName = (requestedMatch?.[1] || titleMatch?.[1]?.replace(/\s*©\s*$/, '') || '').trim();
      const modalSlug = modalName ? slugifyName(modalName.replace(/©|™|®/g, '')) : '';
      const slugLower = communitySlug.toLowerCase();
      const matches = /cancel request/i.test(bodyLower) && (bodyLower.includes(slugLower) || bodyLower.includes(communitySlug.replace(/-/g, ' ')) || (modalSlug && modalSlug === slugLower));
      if (matches) {`;

if (s.includes("const nameFromSlug = communitySlug.replace(/-/g, ' ');")) {
  s = s.replace(oldMatch, newMatch);
  fs.writeFileSync(path, s);
  console.log("Patched cancelRequestOnSkool slugifyName matching");
} else {
  console.log("cancelRequestOnSkool already patched or different structure");
}
