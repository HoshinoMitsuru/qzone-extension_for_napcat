// CDP editor probe: open the Qzone blog editor as the logged-in user,
// dump form controls, optionally fill + submit, and capture the publish
// XHR so we learn the real parameter set.
//   COOKIE_FILE=... TARGET_URL=... MODE=dump|publish node tests/cdp_editor.js
const fs = require("fs");
const WebSocket = require(process.env.WS_MODULE || "ws");

const PORT = process.env.CDP_PORT || 9333;
const COOKIE = process.env.COOKIE_FILE
  ? fs.readFileSync(process.env.COOKIE_FILE, "utf8").trim()
  : (process.env.COOKIE || "");
const TARGET_URL = process.env.TARGET_URL || "";
const MODE = process.env.MODE || "dump";
const TITLE = process.env.TITLE || "toolkit 自测日志";
const BODY = process.env.BODY || "toolkit 自测正文";
const WAIT_MS = Number(process.env.WAIT_MS || 10000);
const UIN = process.env.UIN || "";

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  const ver = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
  const ws = new WebSocket(ver.webSocketDebuggerUrl, { maxPayload: 256 * 1024 * 1024 });
  await new Promise((res, rej) => { ws.once("open", res); ws.once("error", rej); });

  let id = 0;
  const pending = new Map();
  const captured = [];
  ws.on("message", (raw) => {
    const m = JSON.parse(raw.toString());
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id); pending.delete(m.id);
      m.error ? p.reject(new Error(JSON.stringify(m.error))) : p.resolve(m.result);
      return;
    }
    if (m.method === "Network.requestWillBeSent") {
      const q = m.params.request;
      const isWrite = q.method !== "GET";
      if (isWrite || /blognew|blog_new/i.test(q.url)) {
        captured.push({ method: q.method, url: q.url, body: q.postData || "" });
      }
    }
  });
  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const mid = ++id; pending.set(mid, { resolve, reject });
    ws.send(JSON.stringify({ id: mid, method, params, sessionId }));
  });

  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  await send("Network.enable", {}, sessionId);
  await send("Page.enable", {}, sessionId);
  await send("Runtime.enable", {}, sessionId);
  await send("Network.setUserAgentOverride", {
    userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    acceptLanguage: "zh-CN,zh;q=0.9",
    platform: "Win32" }, sessionId);

  for (const p of COOKIE.split(";").map(s => s.trim()).filter(Boolean)) {
    const i = p.indexOf("="); if (i <= 0) continue;
    for (const domain of [".qzone.qq.com", "user.qzone.qq.com"]) {
      try {
        await send("Network.setCookie", {
          name: p.slice(0, i), value: p.slice(i + 1),
          domain, path: "/", secure: true, sameSite: "None" }, sessionId);
      } catch (_) {}
    }
  }

  await send("Emulation.setDeviceMetricsOverride",
    { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false }, sessionId);
  await send("Page.navigate", { url: TARGET_URL }, sessionId);
  await sleep(WAIT_MS);

  const ev0 = async (expr) => {
    const r = await send("Runtime.evaluate",
      { expression: expr, returnByValue: true, awaitPromise: true }, sessionId);
    return r.result && r.result.value;
  };
  // wait until the rich-text area actually has layout
  for (let i = 0; i < 20; i++) {
    const ready = await ev0(`(() => { const e = document.querySelector('#blog-content-text');
      return !!(e && e.offsetHeight > 50); })()`);
    if (ready) break;
    await sleep(1000);
  }

  const ev = async (expr) => {
    const r = await send("Runtime.evaluate",
      { expression: expr, returnByValue: true, awaitPromise: true }, sessionId);
    return r.result && r.result.value;
  };

  console.log("url:", await ev("location.href"));
  const dump = await ev(`(() => {
    const out = { inputs: [], buttons: [], iframes: [], editables: [] };
    document.querySelectorAll('input,textarea').forEach(el => out.inputs.push({
      tag: el.tagName, id: el.id, name: el.name, type: el.type,
      ph: el.placeholder || '', vis: !!(el.offsetWidth || el.offsetHeight) }));
    document.querySelectorAll('button,a,div[class*=btn],span[class*=btn]').forEach(el => {
      const t = (el.innerText || '').trim();
      if (t && t.length < 12) out.buttons.push({ tag: el.tagName, id: el.id, cls: el.className, text: t });
    });
    document.querySelectorAll('iframe').forEach(f => out.iframes.push({ id: f.id, src: (f.src || '').slice(0, 100) }));
    document.querySelectorAll('[contenteditable=true]').forEach(el => out.editables.push({ id: el.id, cls: el.className }));
    return JSON.stringify(out).slice(0, 4000);
  })()`);
  console.log("DOM:", dump);

  if (MODE === "publish") {
    const rect = async (sel) => {
      const r = await ev(`(() => { const e = document.querySelector(${JSON.stringify(sel)});
        if (!e) return null; const b = e.getBoundingClientRect();
        return JSON.stringify({x: b.x + b.width / 2, y: b.y + b.height / 2}); })()`);
      return r ? JSON.parse(r) : null;
    };
    const clickAt = async (pt) => {
      for (const type of ["mousePressed", "mouseReleased"]) {
        await send("Input.dispatchMouseEvent",
          { type, x: pt.x, y: pt.y, button: "left", clickCount: 1 }, sessionId);
      }
      await sleep(400);
    };

    const focusSel = async (sel) => ev(`(() => { const e = document.querySelector(${JSON.stringify(sel)});
      if (!e) return false; e.scrollIntoView({block:'center'}); e.focus();
      return document.activeElement === e || document.activeElement.id === e.id; })()`);

    if (await focusSel("#blog-title-input")) {
      await send("Input.insertText", { text: TITLE }, sessionId);
      console.log("title typed");
    } else {
      console.log("title focus FAILED");
    }
    await sleep(500);
    if (await focusSel("#blog-content-text")) {
      await send("Input.insertText", { text: BODY }, sessionId);
      console.log("body typed");
    } else {
      console.log("body focus FAILED");
    }
    await sleep(1500);

    const state = await ev(`(() => {
      const t = document.querySelector('#blog-title-input');
      const c = document.querySelector('#blog-content-text');
      const b = document.querySelector('#saveBlogButton');
      return JSON.stringify({ title: t && t.value, body: c && c.innerText.slice(0, 40),
        bodyH: c && c.offsetHeight, btnVis: b && !!(b.offsetWidth || b.offsetHeight) });
    })()`);
    console.log("editor state:", state);

    const clicked = await ev(`(() => { const b = document.querySelector('#saveBlogButton');
      if (!b) return 'no-btn'; b.click(); return 'clicked'; })()`);
    console.log("publish:", clicked);
    await sleep(9000);

    // surface any inline error text the editor shows
    const errText = await ev(`(() => {
      const t = document.body.innerText || '';
      const m = t.match(/(错误|失败|不能|请[^\\n]{0,15})/g);
      return m ? [...new Set(m)].slice(0, 6).join(' | ') : '';
    })()`);
    if (errText) console.log("page hints:", errText);
  }

  console.log(`\n=== captured blog requests: ${captured.length} ===`);
  for (const c of captured) {
    console.log(" ", c.method, c.url.slice(0, 200));
    if (c.body) console.log("    body:", c.body.slice(0, 500));
  }
  ws.close(); process.exit(0);
}
main().catch(e => { console.error("ERR:", e.message); process.exit(1); });
