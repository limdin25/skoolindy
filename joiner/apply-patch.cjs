const fs = require("fs");
const path = require("path");
const ROOT = "/root/.openclaw/workspace/community-join-manager";

// === 1. joinCommunity.js ===
let s = fs.readFileSync(path.join(ROOT, "backend/joinCommunity.js"), "utf8");
if (s.includes("slugifyName")) {
  console.log("joinCommunity: already has slugifyName");
} else {
  const slugifyFn = `
function slugifyName(name) {
  return (name || "").toString().trim().replace(/\\s+/g, "-").replace(/[^a-zA-Z0-9-]/g, "").toLowerCase();
}
`;
  s = s.replace("const result = { joined: [], pending: [], error: null };", "const result = { joined: [], pending: [], error: null };\n" + slugifyFn);
  console.log("joinCommunity: added slugifyName");
}

const oldLoop = `    const total = communitySlugs.length;
    onProgress({ status: total ? 'Checking communities' : 'No communities found', current: 0, total });
    for (let i = 0; i < communitySlugs.length; i++) {
      const slug = communitySlugs[i];
      onProgress({ status: \`Visiting community: \${slug}\`, current: i + 1, total, community: slug });
      let status = 'joined';
      try {
        const res2 = await page.goto(\`https://www.skool.com/\${slug}\`, { timeout: 15000 });
        await page.waitForLoadState('domcontentloaded');
        await page.waitForTimeout(2500);
        if (res2 && (res2.status() === 404 || res2.status() >= 500)) { status = 'error'; }
        else if (page.url().toLowerCase().includes('/login')) { status = 'error'; }
        else {
          const txt = (await page.textContent('body')).toLowerCase();
          if (/invite\\s+people|invite\\s+others|invite\\s+members|leave\\s+group/i.test(txt)) status = 'joined';
          else if (/pending/i.test(txt) || /you requested membership/i.test(txt) || /cancel request/i.test(txt)) status = 'pending';
          else status = 'joined';
        }
        if (status === 'pending') result.pending.push(slug);
        else if (status !== 'error') result.joined.push(slug);
      } catch (e) { result.joined.push(slug); }
    }`;

const newLoop = `    const total = communitySlugs.length;
    onProgress({ status: total ? 'Checking communities' : 'No communities found', current: 0, total });
    const settingsBtns = await page.locator('button:has-text("SETTINGS"), button:has-text("Settings")').all();
    if (settingsBtns.length > 0) {
      for (let i = 0; i < settingsBtns.length; i++) {
        const btns = await page.locator('button:has-text("SETTINGS"), button:has-text("Settings")').all();
        if (i >= btns.length) break;
        onProgress({ status: \`Reading community: \${i + 1}/\${settingsBtns.length}\`, current: i + 1, total: settingsBtns.length, community: '...' });
        try {
          const allBtns = await page.locator('button:has-text("SETTINGS"), button:has-text("Settings")').all();
          if (i >= allBtns.length) break;
          await allBtns[i].click();
          await page.waitForTimeout(2500);
          const bodyText = await page.textContent('body');
          const bodyLower = bodyText.toLowerCase();
          const requestedMatch = bodyText.match(/You requested membership to ([^\\n]+?) on (\\d{1,2}\\/\\d{1,2}\\/\\d{4})/i);
          if (requestedMatch) {
            const name = requestedMatch[1].replace(/©|™|®/g, "").trim();
            const requestedAt = requestedMatch[2];
            const slug = slugifyName(name) || (communitySlugs[i] || "").toString();
            result.pending.push({ slug, name, requestedAt });
          } else if (/cancel request/i.test(bodyLower) && /membership/i.test(bodyLower)) {
            const nameMatch = bodyText.match(/Membership settings[\\s\\S]*?([A-Za-z0-9][^\\n]{5,80})/);
            const name = nameMatch ? nameMatch[1].replace(/©|™|®/g, "").trim() : communitySlugs[i];
            result.pending.push({ slug: slugifyName(name) || communitySlugs[i], name: name || communitySlugs[i], requestedAt: null });
          } else {
            const nameMatch = bodyText.match(/Membership settings[\\s\\S]*?([A-Za-z0-9][^\\n]{5,80})/);
            const name = nameMatch ? nameMatch[1].replace(/©|™|®/g, "").trim() : communitySlugs[i];
            result.joined.push({ slug: slugifyName(name) || communitySlugs[i], name: name || communitySlugs[i] });
          }
        } catch (e) {
          const idx = Math.min(i, communitySlugs.length - 1);
          const sl = idx >= 0 ? communitySlugs[idx] : "unknown";
          result.joined.push(typeof sl === "string" ? { slug: sl, name: sl } : sl);
        }
        await page.keyboard.press('Escape');
        await page.waitForTimeout(800);
      }
    } else {
      for (let i = 0; i < communitySlugs.length; i++) {
        const slug = communitySlugs[i];
        onProgress({ status: \`Visiting community: \${slug}\`, current: i + 1, total, community: slug });
        let status = 'joined';
        try {
          const res2 = await page.goto(\`https://www.skool.com/\${slug}\`, { timeout: 15000 });
          await page.waitForLoadState('domcontentloaded');
          await page.waitForTimeout(2500);
          if (res2 && (res2.status() === 404 || res2.status() >= 500)) { status = 'error'; }
          else if (page.url().toLowerCase().includes('/login')) { status = 'error'; }
          else {
            const txt = (await page.textContent('body')).toLowerCase();
            if (/invite\\s+people|invite\\s+others|invite\\s+members|leave\\s+group/i.test(txt)) status = 'joined';
            else if (/pending/i.test(txt) || /you requested membership/i.test(txt) || /cancel request/i.test(txt)) status = 'pending';
            else status = 'joined';
          }
          if (status === 'pending') result.pending.push({ slug, name: slug, requestedAt: null });
          else if (status !== 'error') result.joined.push({ slug, name: slug });
        } catch (e) { result.joined.push({ slug, name: slug }); }
      }
    }`;

if (s.includes("You requested membership to")) {
  console.log("joinCommunity: loop already patched");
} else if (s.includes("result.pending.push(slug)")) {
  s = s.replace(oldLoop, newLoop);
  console.log("joinCommunity: replaced loop");
} else {
  console.error("Could not find old loop - checking content");
  console.log("Has result.pending.push(slug):", s.includes("result.pending.push(slug)"));
  console.log("Has onProgress:", s.includes("onProgress"));
}

if (!s.includes("cancelRequestOnSkool")) {
  const cancelFn = `
async function cancelRequestOnSkool(profileId, communitySlug) {
  const userDataDir = getProfileDir(profileId);
  const context = await chromium.launchPersistentContext(userDataDir, {
    headless: true,
    viewport: { width: 1400, height: 900 },
    args: ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
  });
  const page = context.pages[0] || await context.newPage();
  page.setDefaultTimeout(20000);
  try {
    await page.goto("https://www.skool.com/settings?t=communities", { timeout: 20000 });
    await page.waitForLoadState("domcontentloaded");
    await page.waitForTimeout(3000);
    if (page.url().toLowerCase().includes("/login")) {
      await context.close();
      return { success: false, error: "Session expired" };
    }
    const settingsBtns = await page.locator('button:has-text("SETTINGS"), button:has-text("Settings")').all();
    const slugLower = (communitySlug || "").toLowerCase();
    for (let i = 0; i < settingsBtns.length; i++) {
      const btns = await page.locator('button:has-text("SETTINGS"), button:has-text("Settings")').all();
      if (i >= btns.length) break;
      await btns[i].click();
      await page.waitForTimeout(2000);
      const bodyFull = await page.textContent('body');
      const bodyLower = bodyFull.toLowerCase();
      if (!/cancel request/i.test(bodyLower)) { await page.keyboard.press('Escape'); await page.waitForTimeout(800); continue; }
      const requestedMatch = bodyFull.match(/You requested membership to ([^\\n]+?) on (\\d{1,2}\\/\\d{1,2}\\/\\d{4})/i);
      const modalSlug = requestedMatch ? slugifyName(requestedMatch[1].replace(/©|™|®/g, "").trim()) : "";
      if (modalSlug === slugLower) {
        const cancelLink = page.locator("text=Cancel Request").first();
        if (await cancelLink.isVisible()) {
          await cancelLink.click();
          await page.waitForTimeout(2000);
          await page.keyboard.press('Escape');
          await context.close();
          return { success: true };
        }
      }
      await page.keyboard.press('Escape');
      await page.waitForTimeout(800);
    }
    await context.close();
    return { success: false, error: "Community not found or not pending" };
  } catch (e) {
    try { await context.close(); } catch (_) {}
    return { success: false, error: e.message };
  }
}
`;
  s = s.replace(
    "module.exports = { joinCommunity, checkCommunityMembershipStatus, fetchCommunitiesFromSkoolSettings };",
    cancelFn + "\nmodule.exports = { joinCommunity, checkCommunityMembershipStatus, fetchCommunitiesFromSkoolSettings, cancelRequestOnSkool };"
  );
  console.log("joinCommunity: added cancelRequestOnSkool");
}

fs.writeFileSync(path.join(ROOT, "backend/joinCommunity.js"), s);
console.log("Done: joinCommunity.js");
