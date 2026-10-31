// CDP probe: open a Qzone blog page as the logged-in user, capture every
// network request and JS bundle URL, then grep the bundles for blog publish
// cgi names. Usage:
//   COOKIE="a=1; b=2" UIN=123 node tests/cdp_probe.js
// Needs the `ws` module. If it is not installed next to this script, point
// WS_MODULE at an existing copy, e.g. the one bundled with NapCat:
//   WS_MODULE="/d/NapCat/node_modules/ws" COOKIE_FILE=... node tests/cdp_probe.js
const WebSocket = require(process.env.WS_MODULE || "ws");

const fs = require("fs");
const PORT = process.env.CDP_PORT || 9333;
const COOKIE = process.env.COOKIE_FILE
  ? fs.readFileSync(process.env.COOKIE_FILE, "utf8").trim()
  : (process.env.COOKIE || "");
const UIN = process.env.UIN || "";
const TARGET_URL = process.env.TARGET_URL || `https://user.qzone.qq.com/${UIN}/blog`;
const WAIT_MS = Number(process.env.WAIT_MS || 15000);

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function main() {
  const ver = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
  const ws = new WebSocket(ver.webSocketDebuggerUrl, { maxPayload: 256 * 1024 * 1024 });
  await new Promise((res, rej) => { ws.once("open", res); ws.once("error", rej); });

  let id = 0;
  const pending = new Map();
  const requests = [];
  const responses = [];
  ws.on("message", (raw) => {
    const msg = JSON.parse(raw.toString());
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
      return;
    }
    if (msg.method === "Network.requestWillBeSent") {
      requests.push({ url: msg.params.request.url, type: msg.params.type });
    } else if (msg.method === "Network.responseReceived") {
      responses.push({ url: msg.params.response.url, mime: msg.params.response.mimeType,
                       status: msg.params.response.status });
    }
  });

  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const mid = ++id;
    pending.set(mid, { resolve, reject });
    ws.send(JSON.stringify({ id: mid, method, params, sessionId }));
  });

  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });

  await send("Network.enable", {}, sessionId);
  await send("Page.enable", {}, sessionId);

  // inject cookies (domain cookies for qzone)
  const pairs = COOKIE.split(";").map(s => s.trim()).filter(Boolean);
  let okCookies = 0;
  for (const p of pairs) {
    const i = p.indexOf("=");
    if (i <= 0) continue;
    const name = p.slice(0, i), value = p.slice(i + 1);
    for (const domain of [".qzone.qq.com", "user.qzone.qq.com"]) {
      try {
        await send("Network.setCookie",
          { name, value, domain, path: "/", secure: true, sameSite: "None" }, sessionId);
        okCookies++;
      } catch (_) { /* ignore */ }
    }
  }
  console.log(`cookies injected: ${okCookies}`);

  await send("Page.navigate", { url: TARGET_URL }, sessionId);
  await sleep(WAIT_MS);

  const pageUrl = await send("Runtime.evaluate",
    { expression: "location.href", returnByValue: true }, sessionId).catch(() => null);
  console.log("final url:", pageUrl && pageUrl.result && pageUrl.result.value);

  const blogReqs = requests.filter(r => /blog|日志/i.test(r.url) || /blog/i.test(r.type));
  console.log(`\n=== requests: ${requests.length} total, ${blogReqs.length} blog-related ===`);
  for (const r of blogReqs.slice(0, 40)) console.log(" ", r.type, r.url.slice(0, 160));

  const js = [...new Set(responses.filter(r => /javascript/.test(r.mime || "")).map(r => r.url))];
  console.log(`\n=== JS bundles: ${js.length} ===`);
  js.slice(0, 60).forEach(u => console.log(" ", u.slice(0, 150)));

  // grep bundles for publish cgi names
  const keys = ["blognew", "add_blog", "blog_add", "save_blog", "publish_blog", "blog_publish"];
  console.log("\n=== grep bundles ===");
  for (const u of js) {
    let body;
    try {
      const r = await fetch(u, { headers: { "User-Agent": "Mozilla/5.0", Referer: "https://user.qzone.qq.com/" } });
      body = await r.text();
    } catch (e) { continue; }
    const hits = keys.filter(k => body.includes(k));
    if (hits.length) {
      const k = hits[0];
      const i = body.indexOf(k);
      console.log(` HIT ${u.split("/").pop().slice(0, 40)} [${hits.join(",")}]: ...${body.slice(Math.max(0, i - 120), i + 180).replace(/\s+/g, " ")}`);
    }
  }
  ws.close();
  process.exit(0);
}

main().catch(e => { console.error("PROBE ERROR:", e.message); process.exit(1); });
