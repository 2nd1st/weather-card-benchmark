#!/usr/bin/env node
// 本地预览:serve 静态导出 out/ 并把 /api/om/* 代理到 cache-api。
// 用法: node scripts/serve-local.mjs   (需另起 npm run cache-api)
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SITE = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const OUT = path.join(SITE, "out");
const PORT = Number(process.env.WCB_SITE_PORT || 8930);
const CACHE_API = process.env.WCB_CACHE_API || "http://127.0.0.1:8935";

const MIME = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".webp": "image/webp", ".png": "image/png",
  ".svg": "image/svg+xml", ".txt": "text/plain; charset=utf-8", ".woff2": "font/woff2",
};

http.createServer(async (req, res) => {
  const u = new URL(req.url, `http://localhost:${PORT}`);
  if (u.pathname.startsWith("/api/om/")) {
    try {
      const up = await fetch(CACHE_API + u.pathname + u.search);
      res.writeHead(up.status, { "content-type": up.headers.get("content-type") || "application/json", "access-control-allow-origin": "*" });
      res.end(Buffer.from(await up.arrayBuffer()));
    } catch (e) {
      res.writeHead(502, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: "cache-api unreachable", detail: String(e) }));
    }
    return;
  }
  let p = path.normalize(path.join(OUT, decodeURIComponent(u.pathname)));
  if (!p.startsWith(OUT)) { res.writeHead(403); res.end(); return; }
  if (fs.existsSync(p) && fs.statSync(p).isDirectory()) p = path.join(p, "index.html");
  if (!fs.existsSync(p) && fs.existsSync(p + ".html")) p = p + ".html";
  if (!fs.existsSync(p)) { p = path.join(OUT, "404.html"); res.statusCode = 404; }
  res.setHeader("content-type", MIME[path.extname(p)] || "application/octet-stream");
  fs.createReadStream(p).pipe(res);
}).listen(PORT, () => console.log(`[serve-local] http://localhost:${PORT} (out/ + /api/om→${CACHE_API})`));
