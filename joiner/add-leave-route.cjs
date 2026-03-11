const fs = require('fs');
const path = '/root/.openclaw/workspace/community-join-manager/backend/server.js';
let s = fs.readFileSync(path, 'utf8');

// 1. Update require
s = s.replace(
  "cancelRequestOnSkool } = require('./joinCommunity');",
  "cancelRequestOnSkool, leaveGroupOnSkool } = require('./joinCommunity');"
);

// 2. Add leave route after cancel-request (before fetch-all)
const leaveRoute = `
app.post('/api/communities/leave', async (req, res) => {
  const { profileId, communitySlug } = req.body;
  if (!profileId || !communitySlug) return res.status(400).json({ error: 'profileId and communitySlug required' });
  try {
    const result = await leaveGroupOnSkool(profileId, communitySlug);
    if (result.success) {
      try {
        db.prepare("DELETE FROM profile_communities WHERE profile_id = ? AND lower(slug) = lower(?)").run(profileId, communitySlug);
      } catch (e) {}
      const data = lastFetchResults[profileId];
      if (data && data.joined) {
        const slugLower = communitySlug.toLowerCase();
        data.joined = data.joined.filter(j => ((j.slug || j).toLowerCase()) !== slugLower);
      }
      res.json({ success: true });
    } else {
      res.status(400).json({ success: false, error: result.error || 'Failed to leave' });
    }
  } catch (e) {
    res.status(500).json({ success: false, error: e.message });
  }
});
`;

if (!s.includes("leaveGroupOnSkool")) {
  s = s.replace("cancelRequestOnSkool } = require", "cancelRequestOnSkool, leaveGroupOnSkool } = require");
  console.log("Updated require");
}
if (!s.includes("/api/communities/leave")) {
  const idx = s.indexOf("app.post('/api/communities/fetch-all'");
  s = s.slice(0, idx) + leaveRoute + "\n" + s.slice(idx);
  console.log("Added leave route");
}

fs.writeFileSync(path, s);
console.log("Done");
