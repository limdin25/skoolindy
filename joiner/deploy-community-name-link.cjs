#!/usr/bin/env node
/**
 * Make community name clickable in AccountsTab Communities table.
 * Run on server: cd /root/.openclaw/workspace/community-join-manager && node /path/to/deploy-community-name-link.cjs
 * Or from repo: node deploy-community-name-link.cjs (uses ROOT)
 */
const fs = require('fs');
const path = require('path');

const ROOT = '/root/.openclaw/workspace/community-join-manager';
const ACCOUNTS = path.join(ROOT, 'src/components/AccountsTab.tsx');
const INDEX_CSS = path.join(ROOT, 'src/index.css');

function patch(name, fn) {
  try {
    fn();
    console.log('OK:', name);
  } catch (e) {
    console.error('FAIL:', name, e.message);
  }
}

patch('AccountsTab: wrap community name in link', () => {
  let s = fs.readFileSync(ACCOUNTS, 'utf8');
  if (s.includes('community-link')) return;
  s = s.replace(
    '<TableCell className="text-xs min-w-0 break-words whitespace-normal">{normalizeCommunityForDisplay(c).display_name}</TableCell>',
    `<TableCell className="text-xs min-w-0 break-words whitespace-normal">
  <a
    href={\`https://www.skool.com/\${slug}\`}
    target="_blank"
    rel="noopener noreferrer"
    className="community-link"
  >
    {normalizeCommunityForDisplay(c).display_name}
  </a>
</TableCell>`
  );
  fs.writeFileSync(ACCOUNTS, s);
});

patch('index.css: add community-link styles', () => {
  let s = fs.readFileSync(INDEX_CSS, 'utf8');
  if (s.includes('.community-link')) return;
  s = s.trimEnd() + `

.community-link {
  color: inherit;
  text-decoration: none;
}
.community-link:hover {
  text-decoration: underline;
}
`;
  fs.writeFileSync(INDEX_CSS, s);
});

console.log('Deploy community name link complete.');
