const fs = require('fs');
const path = '/root/.openclaw/workspace/community-join-manager/backend/joinCommunity.js';
let s = fs.readFileSync(path, 'utf8');

const fixes = [
  ["bodyText?.match(/you requested membership to ([^.©]+?)s*[©]?s*ons+/i)", "bodyText?.match(/you requested membership to ([^.©]+?)\\s*[©]?\\s*on\\s+/i)"],
  ["bodyText?.match(/([A-Z0-9][A-Za-z0-9s]+(?:©)?)s*Membership settings/i)", "bodyText?.match(/([A-Z0-9][A-Za-z0-9\\s]+(?:©)?)\\s*Membership settings/i)"],
  [".replace(/s*©s*$/, '')", ".replace(/\\s*©\\s*$/g, '')"]
];
for (const [a, b] of fixes) {
  if (s.includes(a)) { s = s.replace(a, b); console.log("Fixed:", a.substring(0,50)+"..."); }
}

fs.writeFileSync(path, s);
console.log("Done");
