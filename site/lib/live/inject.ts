// ===========================================================================
// lib/live/inject.ts — the DISPLAY-LAYER srcdoc injector (moved from
// app/live/inject.ts in v3 §2.5). Live cards render in a
// sandbox="allow-scripts" iframe with NO allow-same-origin (model output is
// untrusted code → opaque origin). The opaque origin has THREE consequences,
// all solved here WITHOUT touching the model HTML on disk (data/ originals stay
// one byte unchanged — we only transform the string handed to `srcdoc`):
//
//  1. Relative fetch resolution. Cards fetch `/api/om/{forecast,archive}` (a
//     frozen prompt contract). Under `srcdoc` the document URL is about:srcdoc
//     with no valid base URL, so we inject `<base href>` so `/api/om/*` (and
//     any other relative DOM URL) resolves. Interactive live mode points the
//     base at the serving origin (Caddy / dev rewrite → cache-api / Next
//     /api/om route, CORS "*"); offline arena mode points it at an https
//     placeholder that the fetch/XHR stub answers from an embedded snapshot.
//
//  2. `new URL(...)` construction. MANY cards build their request URL with
//     `new URL(endpoint, window.location.origin)` or `new URL(endpoint,
//     window.location.href)`. Under an opaque-origin srcdoc, `location.origin`
//     is the string "null" and `location.href` is "about:srcdoc" — neither is a
//     usable base, so the URL constructor throws "Failed to construct 'URL':
//     Invalid base URL" (or "Invalid URL" for a bare relative 1-arg form) and
//     the card dies before it ever fetches. `<base href>` does NOT help: cards
//     read `window.location.*` directly, which stays opaque regardless. So we
//     reassign `window.URL` to a wrapper that, when the authored construction
//     throws (bad/opaque base, relative 1-arg), retries against our injected
//     base — and, when a card reads the document URL to pull query params
//     (`new URL(location.href).searchParams`), hands back our injected search.
//
//  3. City/date delivery. Cards read lat/lon/name/date from
//     `new URLSearchParams(window.location.search)`, which is empty under
//     srcdoc. A tiny shim makes the empty-string / current-search construction
//     yield our params, leaving every other construction untouched.
//
// (v3 §2.5) `stubFetch`: a deterministic, OFFLINE fetch/XHR interceptor that
// answers `/api/om/*` (and absolute Open-Meteo `/v1/*`, matched by PATHNAME not
// origin) from an embedded WeatherSnapshot. Arena (server-side, no origin) and
// gallery/compare live modes use it so both sides of a blind pair see
// byte-identical weather with no network. `buildSrcdoc` gains an options object;
// the legacy positional form is kept as an overload for the app/live + app/card
// callers.
//
// Everything here is pure string work — safe to call server-side (it never
// touches window.location at build time).
// ===========================================================================

export interface LiveParams {
  /** decimal string, byte-exact from the manifest (or a decorative city). */
  lat: string;
  lon: string;
  name: string;
  /** ISO yyyy-mm-dd. */
  date: string;
}

/** One (endpoint, location, date) fixture entry — the shape of a
 *  weather-snapshot.json location (extra fields like `canary` are ignored). */
export interface WeatherSnapshotLocation {
  /** "forecast" | "archive" (the two cache-api endpoints). */
  endpoint: string;
  city: string;
  /** ISO yyyy-mm-dd. */
  date: string;
  /** decimal STRING (frozen form) or plain decimal. */
  lat: string;
  lon: string;
  /** Open-Meteo wire payload (decimal-string encoded is fine — the stub
   *  re-materializes numbers, matching cache-api server.mjs). */
  response: unknown;
}

/** The fixture payload consumed by `stubFetch` / `buildSrcdoc`. Structurally a
 *  subset of the loaded weather-snapshot.json (lib/data WeatherSnapshot is
 *  assignable to this), so an arena caller can pass a loaded snapshot as-is. */
export interface WeatherSnapshot {
  locations: WeatherSnapshotLocation[];
  /** Optional explicit display location; else derived from the first
   *  forecast location, else `locations[0]`. */
  primary?: LiveParams;
}

/** Offline base origin used when no serving origin is supplied (arena / blind
 *  pairs). The stub matches by pathname, so the origin only has to be a VALID
 *  absolute URL base — it is never actually contacted. */
const OFFLINE_BASE = "https://wcb.local";

/** Normalise a serving origin (or the offline placeholder) to a bare origin
 *  string with no trailing slash, e.g. "http://localhost:3000". */
function baseOrigin(origin?: string): string {
  const o = (origin && origin.length > 0 ? origin : OFFLINE_BASE).trim();
  return o.replace(/\/+$/, "");
}

/** Build the `?lat=..&lon=..&name=..&date=..` search string the card will read. */
export function paramsToSearch(p: LiveParams): string {
  const q = new URLSearchParams({
    lat: p.lat,
    lon: p.lon,
    name: p.name,
    date: p.date,
  });
  return `?${q.toString()}`;
}

/** Derive the display params for a snapshot: explicit `primary`, else the
 *  first forecast location, else the first location. Null when empty. */
export function snapshotDisplayParams(snapshot: WeatherSnapshot): LiveParams | null {
  if (snapshot.primary) return snapshot.primary;
  const locs = snapshot.locations ?? [];
  const loc = locs.find((l) => l.endpoint === "forecast") ?? locs[0];
  if (!loc) return null;
  return { lat: String(loc.lat), lon: String(loc.lon), name: loc.city, date: loc.date };
}

// ---------------------------------------------------------------------------
// urlBaseShim — repair `new URL(...)` under an opaque-origin srcdoc.
// ---------------------------------------------------------------------------

/**
 * Returns a `<script>` prelude that reassigns `window.URL` to a wrapper. The
 * wrapper first tries the authored construction verbatim; if that throws
 * (opaque `location.origin === "null"`, `location.href === "about:srcdoc"`, an
 * undefined base, or a bare relative 1-arg URL) it retries against `base` so
 * `/api/om/*` resolves instead of the card dying. When a card constructs a URL
 * from the document location purely to read query params
 * (`new URL(location.href).searchParams`), the wrapper hands back a URL carrying
 * `search` so those params reach the card. Every native URL instance, static
 * (createObjectURL, canParse, …) and `instanceof` check is preserved.
 */
function urlBaseShim(base: string, search: string): string {
  return (
    `<script>(function(){` +
    `var N=window.URL;if(!N){return;}` +
    `var BASE=${JSON.stringify(base + "/")};` +
    `var SEARCH=${JSON.stringify(search || "")};` +
    `function isDoc(u){if(u==null){return false;}var s;try{s=String(u);}catch(e){return false;}` +
    `return s===""||s==="about:srcdoc"||s.indexOf("about:srcdoc")===0;}` +
    `function build(url,base,hasBase){` +
    // param-delivery: reading the document URL → hand back BASE+SEARCH
    `if(!hasBase&&SEARCH&&isDoc(url)){try{return new N(SEARCH,BASE);}catch(e){}}` +
    `try{return hasBase?new N(url,base):new N(url);}catch(e1){` +
    `try{return new N(url,BASE);}catch(e2){return new N(String(url),BASE);}}}` +
    `function U(url,base){return arguments.length>=2?build(url,base,true):build(url,undefined,false);}` +
    `U.prototype=N.prototype;` +
    `try{Object.getOwnPropertyNames(N).forEach(function(k){` +
    `if(k==="length"||k==="name"||k==="prototype"){return;}` +
    `try{var v=N[k];U[k]=(typeof v==="function")?v.bind(N):v;}catch(e){}});}catch(e){}` +
    `try{window.URL=U;}catch(e){}` +
    `})();</script>`
  );
}

// ---------------------------------------------------------------------------
// stubFetch — offline /api/om/* interceptor installed inside the srcdoc.
// ---------------------------------------------------------------------------

/**
 * Returns a `<script>` prelude that intercepts `fetch()` and `XMLHttpRequest`
 * for the weather endpoints inside the srcdoc and answers from the snapshot —
 * deterministic, offline, identical on both sides of a blind pair. Endpoints are
 * matched by PATHNAME (`/api/om/{forecast,archive}` AND absolute Open-Meteo
 * `/v1/{forecast,archive}`), so origin/base differences never miss. Decimal-
 * string numbers are re-materialized to JSON numbers (mirrors cache-api
 * server.mjs). Non-weather requests pass through untouched.
 */
export function stubFetch(snapshot: WeatherSnapshot): string {
  const locations = (snapshot.locations ?? []).map((l) => ({
    endpoint: l.endpoint,
    lat: String(l.lat),
    lon: String(l.lon),
    date: String(l.date),
    response: l.response ?? {},
  }));
  const payload = JSON.stringify(locations);
  // Runtime interceptor (kept ES5-ish so ancient card scripts don't trip it).
  return (
    `<script>(function(){` +
    `var LOC=${payload};` +
    `var NU=window.URL;` +
    `var DR=/^-?\\d+(?:\\.\\d+)?$/;` +
    `function mat(v){` +
    `if(typeof v==='string'){return DR.test(v)&&isFinite(Number(v))?Number(v):v;}` +
    `if(Array.isArray(v)){return v.map(mat);}` +
    `if(v&&typeof v==='object'){var o={};for(var k in v){if(Object.prototype.hasOwnProperty.call(v,k)){o[k]=mat(v[k]);}}return o;}` +
    `return v;}` +
    // endpoint name from a pathname: /api/om/forecast OR /v1/forecast (open-meteo)
    `function epName(p){if(/\\/(api\\/om|v1)\\/forecast/.test(p)){return 'forecast';}` +
    `if(/\\/(api\\/om|v1)\\/archive/.test(p)){return 'archive';}return null;}` +
    `function pathOf(u){try{return new NU(u,'https://x/').pathname;}catch(e){return String(u||'');}}` +
    `function isOm(u){if(!u){return false;}return epName(pathOf(u))!=null;}` +
    `function urlOf(i){if(typeof i==='string'){return i;}if(i&&i.url){return i.url;}try{return String(i);}catch(e){return '';}}` +
    `function near(a,b){a=Number(a);b=Number(b);return isFinite(a)&&isFinite(b)&&Math.abs(a-b)<=1e-3;}` +
    `function pick(u){try{` +
    `var url=new NU(u,'https://x/');var ep=epName(url.pathname);if(!ep){return {};}` +
    `var q=url.searchParams;` +
    `var lat=q.get('latitude')||q.get('lat');var lon=q.get('longitude')||q.get('lon');` +
    `var date=q.get('start_date')||q.get('date');` +
    `var cand=LOC.filter(function(l){return l.endpoint===ep;});` +
    `var hit=cand.filter(function(l){return (lat==null||near(l.lat,lat))&&(lon==null||near(l.lon,lon))&&(date==null||String(l.date)===String(date));})[0];` +
    `if(!hit){hit=cand.filter(function(l){return (lat==null||near(l.lat,lat))&&(lon==null||near(l.lon,lon));})[0];}` +
    `if(!hit){hit=cand.filter(function(l){return (date==null||String(l.date)===String(date));})[0];}` +
    `if(!hit){hit=cand[0];}if(!hit){hit=LOC[0];}` +
    `return hit?mat(hit.response||{}):{};` +
    `}catch(e){return {};}}` +
    // fetch
    `var _f=window.fetch;` +
    `window.fetch=function(input){var url=urlOf(input);if(isOm(url)){` +
    `var body=JSON.stringify(pick(url));` +
    `return Promise.resolve(new Response(body,{status:200,headers:{'Content-Type':'application/json'}}));` +
    `}return _f?_f.apply(this,arguments):Promise.reject(new Error('offline'));};` +
    // XHR
    `var OX=window.XMLHttpRequest;` +
    `if(OX){function SX(){var xhr=new OX();var _open=xhr.open,_send=xhr.send,_url='',_stub=false;` +
    `xhr.open=function(method,u){_url=u;_stub=isOm(String(u));return _open.apply(xhr,arguments);};` +
    `xhr.send=function(){if(_stub){var obj=pick(_url);var text=JSON.stringify(obj);` +
    `var rt=xhr.responseType;var respVal=(rt==='json')?obj:text;` +
    `try{Object.defineProperty(xhr,'readyState',{value:4,configurable:true});` +
    `Object.defineProperty(xhr,'status',{value:200,configurable:true});` +
    `Object.defineProperty(xhr,'statusText',{value:'OK',configurable:true});` +
    `Object.defineProperty(xhr,'responseText',{value:text,configurable:true});` +
    `Object.defineProperty(xhr,'response',{value:respVal,configurable:true});}catch(e){}` +
    `setTimeout(function(){` +
    `if(typeof xhr.onreadystatechange==='function'){try{xhr.onreadystatechange();}catch(e){}}` +
    `if(typeof xhr.onload==='function'){try{xhr.onload();}catch(e){}}` +
    `try{if(xhr.dispatchEvent){xhr.dispatchEvent(new Event('readystatechange'));xhr.dispatchEvent(new Event('load'));xhr.dispatchEvent(new Event('loadend'));}}catch(e){}` +
    `},0);return;}return _send.apply(xhr,arguments);};` +
    `return xhr;}SX.prototype=OX.prototype;try{window.XMLHttpRequest=SX;}catch(e){}}` +
    `})();</script>`
  );
}

// ---------------------------------------------------------------------------
// bboxReporter — measure the card's tight content rect and report it to parent.
// ---------------------------------------------------------------------------

/**
 * Returns a `<script>` prelude (same composable shape as `urlBaseShim` /
 * `stubFetch`) that, after the card has painted, measures the tight bounding
 * rect of the MAIN CONTENT inside the 1280×800 srcdoc and `postMessage`s it to
 * the parent as `{type:'wcb:bbox', nonce, rect:{x,y,w,h}}`. The "content-fit"
 * exhibit mode (ScaledExhibit `fit="content"`) uses this to zoom the pane onto
 * the card instead of showing a small card floating in dead viewport.
 *
 * Heuristic (no stored per-slot geometry exists — must be measured at runtime):
 * consider VISIBLE elements (nonzero area, not display:none / visibility:hidden,
 * opacity > 0.05) that descend from <body> at depth ≤ 3; DROP any element
 * covering > 92% of the viewport area (full-bleed page-background wrappers) —
 * unless nothing smaller survives, in which case keep them; UNION the survivors
 * and clip to the viewport. Reports once after load+~400ms (settle) and again at
 * ~1600ms (late JS that builds the card after first paint).
 *
 * The `nonce` is baked in per-mount and echoed back so the listener can reject
 * cross-frame chatter (multiple exhibits share one window `message` bus). A card
 * faking a bbox is cosmetic-only — the nonce merely prevents accidental mix-ups.
 * Kept ES5-ish so ancient card scripts don't trip it.
 */
export function bboxReporter(nonce: string): string {
  return (
    `<script>(function(){` +
    `var NONCE=${JSON.stringify(nonce)};` +
    `function vp(){return {w:(window.innerWidth||document.documentElement.clientWidth||1280),` +
    `h:(window.innerHeight||document.documentElement.clientHeight||800)};}` +
    `function vis(el){var s;try{s=getComputedStyle(el);}catch(e){return false;}if(!s){return false;}` +
    `if(s.display==='none'||s.visibility==='hidden'||s.visibility==='collapse'){return false;}` +
    `if(parseFloat(s.opacity||'1')<=0.05){return false;}` +
    `var r=el.getBoundingClientRect();return r.width>0&&r.height>0;}` +
    // collect visible rects of body descendants at depth 1..3.
    `function collect(){var out=[];var body=document.body;if(!body){return out;}` +
    `function walk(el,depth){if(depth>=3){return;}var kids=el.children;if(!kids){return;}` +
    `for(var i=0;i<kids.length;i++){var c=kids[i];if(vis(c)){out.push(c.getBoundingClientRect());}` +
    `walk(c,depth+1);}}` +
    `walk(body,0);return out;}` +
    `function compute(){var rects=collect();var v=vp();var VW=v.w,VH=v.h;var vArea=VW*VH;` +
    `var clip=[];for(var i=0;i<rects.length;i++){var r=rects[i];` +
    `var x0=Math.max(0,r.left),y0=Math.max(0,r.top),x1=Math.min(VW,r.right),y1=Math.min(VH,r.bottom);` +
    `if(x1>x0&&y1>y0){clip.push({x0:x0,y0:y0,x1:x1,y1:y1,a:(x1-x0)*(y1-y0)});}}` +
    `if(!clip.length){return null;}` +
    // drop full-bleed background wrappers (>92% viewport area) unless nothing smaller remains.
    `var small=[];for(var j=0;j<clip.length;j++){if(clip[j].a<=0.92*vArea){small.push(clip[j]);}}` +
    `var use=small.length?small:clip;` +
    `var minx=Infinity,miny=Infinity,maxx=-Infinity,maxy=-Infinity;` +
    `for(var k=0;k<use.length;k++){var c=use[k];` +
    `if(c.x0<minx){minx=c.x0;}if(c.y0<miny){miny=c.y0;}if(c.x1>maxx){maxx=c.x1;}if(c.y1>maxy){maxy=c.y1;}}` +
    `minx=Math.max(0,minx);miny=Math.max(0,miny);maxx=Math.min(VW,maxx);maxy=Math.min(VH,maxy);` +
    `var w=maxx-minx,h=maxy-miny;if(!(w>0)||!(h>0)){return null;}` +
    `return {x:minx,y:miny,w:w,h:h};}` +
    `function report(){try{var r=compute();if(r){parent.postMessage({type:'wcb:bbox',nonce:NONCE,rect:r},'*');}}catch(e){}}` +
    `function go(){setTimeout(report,400);setTimeout(report,1600);}` +
    `if(document.readyState==='complete'){go();}else{window.addEventListener('load',go);}` +
    `})();</script>`
  );
}

/** Inject `bboxReporter(nonce)` into an already-built srcdoc string (the exhibit
 *  layer owns the per-mount nonce, so it composes the reporter AFTER buildSrcdoc
 *  rather than through it). Placement mirrors the other preludes (after <head>). */
export function injectBboxReporter(srcdoc: string, nonce: string): string {
  return injectAfterHead(srcdoc, bboxReporter(nonce));
}

// ---------------------------------------------------------------------------
// buildSrcdoc — assemble the final srcdoc string.
// ---------------------------------------------------------------------------

export interface BuildSrcdocOpts {
  /** serving origin, e.g. "http://localhost:3000". Server-side callers OMIT it
   *  (the stub answers relative /api/om/* offline). Never read from
   *  window.location here — that would break server-side rendering. */
  origin?: string;
  /** fixture for offline /api/om/* answers (deterministic both sides). */
  snapshot?: WeatherSnapshot;
  /** simulated API latency (display layer; makes loading states observable). */
  delayMs?: number;
  /** explicit display params; else derived from the snapshot's primary location. */
  params?: LiveParams;
}

function delayShimScript(delayMs: number): string {
  if (!delayMs || delayMs <= 0) return "";
  return (
    `<script>(function(){var D=${Math.floor(delayMs)};var F=window.fetch;` +
    `window.fetch=function(){var a=arguments,t=this;` +
    `return new Promise(function(res,rej){setTimeout(function(){` +
    `F.apply(t===undefined?window:t,a).then(res,rej);},D);});};})();</script>`
  );
}

function urlSearchShim(search: string): string {
  if (!search) return "";
  return (
    `<script>(function(){` +
    `var S=${JSON.stringify(search)};` +
    `var O=window.URLSearchParams;` +
    `function P(i){` +
    `if(i===undefined||i===null||i===""||i===window.location.search){return new O(S);}` +
    `return new O(i);` +
    `}` +
    `P.prototype=O.prototype;` +
    `try{window.URLSearchParams=P;}catch(e){}` +
    `})();</script>`
  );
}

function injectAfterHead(html: string, inject: string): string {
  const headOpen = html.match(/<head[^>]*>/i);
  if (headOpen && headOpen.index != null) {
    const at = headOpen.index + headOpen[0].length;
    return html.slice(0, at) + inject + html.slice(at);
  }
  const htmlOpen = html.match(/<html[^>]*>/i);
  if (htmlOpen && htmlOpen.index != null) {
    const at = htmlOpen.index + htmlOpen[0].length;
    return html.slice(0, at) + inject + html.slice(at);
  }
  return inject + html;
}

/**
 * Transform raw card HTML into the string handed to `iframe.srcdoc`.
 *
 * NEW form (v3): `buildSrcdoc(html, { origin?, snapshot?, delayMs?, params? })`.
 *   - `origin` → `<base href>` + URL-wrapper base (interactive live). Omit
 *     server-side; the offline placeholder is used and the stub answers.
 *   - `snapshot` → offline `stubFetch` interceptor + display params derived
 *     from the snapshot (unless `params` overrides).
 *
 * LEGACY form (overload): `buildSrcdoc(html, origin, search, delayMs?)` — kept
 * for the app/live + app/card callers.
 */
export function buildSrcdoc(html: string, opts?: BuildSrcdocOpts): string;
export function buildSrcdoc(
  html: string,
  origin: string,
  search: string,
  delayMs?: number,
): string;
export function buildSrcdoc(
  html: string,
  a?: BuildSrcdocOpts | string,
  b?: string,
  c?: number,
): string {
  // -------- legacy positional form --------
  if (typeof a === "string") {
    const origin = a;
    const search = b ?? "";
    const delayMs = c ?? 0;
    const base = baseOrigin(origin);
    const inject =
      `<base href="${base}/">` +
      urlBaseShim(base, search) +
      urlSearchShim(search) +
      delayShimScript(delayMs);
    return injectAfterHead(html, inject);
  }

  // -------- new options form --------
  const opts = a ?? {};
  const base = baseOrigin(opts.origin);

  const params =
    opts.params ?? (opts.snapshot ? snapshotDisplayParams(opts.snapshot) : null);
  const search = params ? paramsToSearch(params) : "";

  const parts: string[] = [];
  // <base href> always: relative /api/om/* (and any relative DOM URL) resolves.
  parts.push(`<base href="${base}/">`);
  // URL-wrapper: repair new URL(x, location.origin/href) + deliver params via URL.
  parts.push(urlBaseShim(base, search));
  // URLSearchParams shim: deliver params to new URLSearchParams(location.search).
  if (search) parts.push(urlSearchShim(search));
  // Offline weather interceptor (arena / gallery live with a snapshot).
  if (opts.snapshot) parts.push(stubFetch(opts.snapshot));
  // Simulated latency (wraps whatever fetch is current — stub or real).
  if (opts.delayMs && opts.delayMs > 0) parts.push(delayShimScript(opts.delayMs));

  return injectAfterHead(html, parts.join(""));
}
