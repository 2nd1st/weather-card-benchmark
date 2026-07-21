"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type Key,
} from "react";
import {
  buildSrcdoc,
  injectBboxReporter,
  type WeatherSnapshot,
} from "@/lib/live/inject";

// CardFrame (spec §5) + the shared measured-viewport exhibit primitive reused by
// EVERY live-card surface (gallery live tiles, /compare, arena+gate pairs, the
// card page). Model output is UNTRUSTED code: it renders ONLY inside
// <iframe sandbox="allow-scripts" srcdoc> with NO allow-same-origin (opaque
// origin) — never inlined into our DOM (non-neg #3).
//
// ── WHY 1280×800 (ScaledExhibit) ────────────────────────────────────────────
// Every card.html was authored AND measured by the runner at a FIXED render
// viewport of 1280×800 (runner/render/env_digest.py pins VIEWPORT; all 708
// shot.webp are exactly 1280×800). A sandboxed iframe otherwise adopts whatever
// CSS size its pane happens to be as its INTERNAL viewport, so a card measured
// at 1280×800 renders wrong at any other size — fixed-width cards shrink to a
// narrow strip with dead space, wide cards hard-clip at the edges, mobile frames
// crop. The fix is to give the iframe a FIXED internal viewport of 1280×800
// (style width/height in px) and then scale the whole frame down to the pane
// with a CSS transform. The card lays out under the exact pixels it was built
// for; the exhibit fills the pane edge-to-edge at every size (desktop AND
// ≤700px mobile), no dead space, no clip. Scaled frames stay fully interactive
// (no pointer-events blocking) — matching the runner's live conditions.

export const EXHIBIT_W = 1280;
export const EXHIBIT_H = 800;

/** Content bbox reported by the injected reporter, in 1280×800 stage coords. */
interface BBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

function makeNonce(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

/**
 * ScaledExhibit — THE measured-viewport exhibit. Mounts an untrusted `srcdoc`
 * in an iframe pinned to a 1280×800 internal viewport, scaled to the pane.
 *
 *  - fill mode (default): scale = paneWidth / 1280; the wrapper reserves exactly
 *    the painted height (800 × scale) so surrounding layout collapses to the
 *    real height (no phantom gap) and the exhibit fills the pane width-wise. Give
 *    the wrapping element the same 1280/800 aspect (aspect-ratio: 1280/800) and
 *    it fills edge-to-edge; give it a different aspect and vertical overflow is
 *    clipped, top-aligned (fine for gallery tiles).
 *  - letterbox mode: the parent owns the box height; the 1280×800 frame is fit
 *    (min scale) and centered inside it. Use when a consumer's media area is not
 *    16:10 and clipping/top-align is unacceptable.
 *
 * ── fit="viewport" | "content" ──────────────────────────────────────────────
 * `fit` is orthogonal to the fill/letterbox axis above.
 *  - fit="viewport" (default): the behavior described above — the whole 1280×800
 *    render viewport is shown. Use next to a 1280×800 screenshot (card page).
 *  - fit="content": MANY cards render a small card block floating in a sea of
 *    dead 1280×800 background, so the pane shows a tiny card. In content mode we
 *    inject a reporter (lib/live injectBboxReporter) that measures the card's
 *    tight content rect at runtime and posts it back; we then transform the inner
 *    stage (scale + translate) so that rect fills the pane (~4% padding, centered
 *    both axes). Scale is clamped to [full-viewport-fit, 2.4] so a card that
 *    already fills the viewport is left alone and a tiny card is not zoomed into
 *    a blurry mess. If no valid report arrives, we stay at full-viewport fit
 *    (graceful for cards whose JS died — no reporter, no message → default).
 *
 * The `srcdoc` is the FULLY-BUILT document (already run through lib/live
 * buildSrcdoc by the caller/server) — this component only measures + scales
 * (and, in content mode, composes the reporter onto it with a per-mount nonce).
 */
export function ScaledExhibit({
  srcdoc,
  title,
  onLoad,
  frameKey,
  letterbox = false,
  fit = "viewport",
  className,
  iframeBackground = "transparent",
  onContentRect,
}: {
  /** fully-built sandbox document (base-href / fetch-stub already injected). */
  srcdoc: string;
  title: string;
  /** fires when the iframe document loads (vote-gate / load accounting). */
  onLoad?: () => void;
  /** re-key the iframe to force a fresh document (re-run card scripts). */
  frameKey?: Key;
  /** center-fit a 1280×800 frame inside a non-16:10 box instead of filling width. */
  letterbox?: boolean;
  /** "viewport" shows the whole 1280×800 render; "content" zooms onto the card. */
  fit?: "viewport" | "content";
  className?: string;
  /** iframe backdrop behind any transparent card pixels (card page uses #fff). */
  iframeBackground?: string;
  /** content mode: surfaces each accepted bbox report (stage coords) to the host —
   *  lets a host adapt its pane aspect to the content (arena pair panes). */
  onContentRect?: (rect: BBox | null) => void;
}) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const [box, setBox] = useState<{ w: number; h: number }>({ w: 0, h: 0 });

  const isContent = fit === "content";

  // Measure the pane (ResizeObserver + an initial synchronous read) so the scale
  // is correct on first paint and tracks every resize.
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const measure = () => setBox({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // content mode: a per-mount nonce, refreshed whenever the document changes
  // (new card or re-keyed re-run) so a fresh reporter re-measures the fresh DOM.
  const nonce = useMemo(() => makeNonce(), [srcdoc, frameKey, isContent]);
  const finalSrcdoc = useMemo(
    () => (isContent ? injectBboxReporter(srcdoc, nonce) : srcdoc),
    [isContent, srcdoc, nonce],
  );

  // content mode: listen for the nonce-matched bbox report(s). We accept at most
  // two (the load+400ms settle and the ~1600ms late-JS re-check), then stop.
  const [contentRect, setContentRect] = useState<BBox | null>(null);
  const msgCount = useRef(0);
  useEffect(() => {
    setContentRect(null);
    msgCount.current = 0;
  }, [nonce]);
  useEffect(() => {
    if (!isContent) return;
    function onMsg(e: MessageEvent) {
      const win = iframeRef.current?.contentWindow;
      if (win && e.source !== win) return;
      const d = e.data as { type?: string; nonce?: string; rect?: BBox } | null;
      if (!d || d.type !== "wcb:bbox" || d.nonce !== nonce) return;
      const r = d.rect;
      if (!r || !(r.w > 0) || !(r.h > 0)) return;
      if (msgCount.current >= 2) return;
      msgCount.current += 1;
      const rect = { x: +r.x || 0, y: +r.y || 0, w: +r.w, h: +r.h };
      setContentRect(rect);
      onContentRect?.(rect);
    }
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, [isContent, nonce]);

  // viewport-mode scale (fill or letterbox) — UNCHANGED from the original.
  const vpScale = letterbox
    ? box.w > 0 && box.h > 0
      ? Math.min(box.w / EXHIBIT_W, box.h / EXHIBIT_H)
      : 0
    : box.w > 0
      ? box.w / EXHIBIT_W
      : 0;

  // content-mode: full-viewport-fit scale = the lower clamp (never zoom out past
  // showing the whole render). Panes here are 1280/800, so this fills the pane.
  const fitScale = box.w > 0 ? box.w / EXHIBIT_W : 0;
  const contentT = useMemo(() => {
    if (!isContent || !contentRect || box.w <= 0 || box.h <= 0) return null;
    const PAD = 0.04; // ~4% padding around the content on every side.
    const availW = box.w * (1 - 2 * PAD);
    const availH = box.h * (1 - 2 * PAD);
    const raw = Math.min(availW / contentRect.w, availH / contentRect.h);
    const s = Math.max(box.w / EXHIBIT_W, Math.min(raw, 2.4));
    const cx = contentRect.x + contentRect.w / 2;
    const cy = contentRect.y + contentRect.h / 2;
    // Clamp the translation so the 1280×800 stage always covers the pane —
    // an off-center bbox must never drag a stage edge into view (background
    // seams). If the stage is smaller than the pane on an axis, center it.
    const sw = EXHIBIT_W * s;
    const sh = EXHIBIT_H * s;
    let tx = box.w / 2 - cx * s;
    let ty = box.h / 2 - cy * s;
    tx = sw >= box.w ? Math.min(0, Math.max(box.w - sw, tx)) : (box.w - sw) / 2;
    ty = sh >= box.h ? Math.min(0, Math.max(box.h - sh, ty)) : (box.h - sh) / 2;
    return { s, tx, ty };
  }, [isContent, contentRect, box.w, box.h]);

  const ready = isContent ? fitScale > 0 : vpScale > 0;

  // content mode: REVEAL-AFTER-FRAMED. The card stays invisible until its
  // framing transform is settled (first bbox report, or a fallback timeout for
  // cards that never report) — the viewer sees a clean fade-in of the framed
  // card instead of a full-viewport render visibly zooming onto the content.
  // The host's onLoad (vote gating / shimmer) is deferred to the reveal, so
  // loading states hold until the card is actually visible.
  const [frameLoaded, setFrameLoaded] = useState(false);
  const [revealed, setRevealed] = useState(!isContent);
  const onLoadRef = useRef(onLoad);
  onLoadRef.current = onLoad;
  useEffect(() => {
    setFrameLoaded(false);
    setRevealed(!isContent);
  }, [nonce, isContent]);
  useEffect(() => {
    if (isContent && contentRect && !revealed) setRevealed(true);
  }, [isContent, contentRect, revealed]);
  useEffect(() => {
    if (!isContent || !frameLoaded || revealed) return;
    const t = setTimeout(() => setRevealed(true), 1900);
    return () => clearTimeout(t);
  }, [isContent, frameLoaded, revealed]);
  const revealNotified = useRef(false);
  useEffect(() => {
    revealNotified.current = false;
  }, [nonce]);
  useEffect(() => {
    if (isContent && revealed && frameLoaded && !revealNotified.current) {
      revealNotified.current = true;
      onLoadRef.current?.();
    }
  }, [isContent, revealed, frameLoaded]);
  const handleFrameLoad = () => {
    if (isContent) setFrameLoaded(true);
    else onLoad?.();
  };

  const wrapStyle: CSSProperties = isContent
    ? // pane owns its aspect/height (host container: 1280/800 or host-provided);
      // we fill + clip it and transform the inner stage to frame the content.
      { position: "absolute", inset: 0, overflow: "hidden" }
    : letterbox
      ? { position: "relative", width: "100%", height: "100%", overflow: "hidden" }
      : {
          position: "relative",
          width: "100%",
          // reserve the exact painted height so no phantom gap sits below the frame.
          height: vpScale > 0 ? EXHIBIT_H * vpScale : undefined,
          overflow: "hidden",
        };

  const frameStyle: CSSProperties = isContent
    ? {
        position: "absolute",
        top: 0,
        left: 0,
        width: `${EXHIBIT_W}px`,
        height: `${EXHIBIT_H}px`,
        transform: contentT
          ? `translate(${contentT.tx}px, ${contentT.ty}px) scale(${contentT.s})`
          : `scale(${fitScale})`,
        transformOrigin: "top left",
        // no motion before the reveal (framing snaps while invisible); after it,
        // only the rare late settle-report animates — gently.
        transition: revealed
          ? "transform 240ms ease, opacity 200ms ease"
          : "opacity 200ms ease",
        opacity: revealed ? 1 : 0,
        border: 0,
        background: iframeBackground,
      }
    : {
        position: "absolute",
        top: letterbox ? (box.h - EXHIBIT_H * vpScale) / 2 : 0,
        left: letterbox ? (box.w - EXHIBIT_W * vpScale) / 2 : 0,
        width: `${EXHIBIT_W}px`,
        height: `${EXHIBIT_H}px`,
        transform: `scale(${vpScale})`,
        transformOrigin: "top left",
        border: 0,
        background: iframeBackground,
      };

  return (
    <div ref={boxRef} className={className} style={wrapStyle}>
      {ready ? (
        <iframe
          key={frameKey}
          ref={iframeRef}
          title={title}
          sandbox="allow-scripts"
          srcDoc={finalSrcdoc}
          onLoad={handleFrameLoad}
          style={frameStyle}
        />
      ) : null}
    </div>
  );
}

const htmlCache = new Map<string, Promise<string>>();
function fetchCard(url: string): Promise<string> {
  let p = htmlCache.get(url);
  if (!p) {
    p = fetch(url).then((r) => {
      if (!r.ok) throw new Error(`card.html ${r.status}`);
      return r.text();
    });
    htmlCache.set(url, p);
  }
  return p;
}

export function CardFrame({
  cardUrl,
  title,
  thumbUrl,
  snapshot,
  origin,
  delayMs = 0,
  active = true,
  lazy = true,
  generation = 0,
  letterbox = false,
  fit = "viewport",
  unavailableLabel = "card unavailable",
}: {
  /** public URL of the ORIGINAL card.html (never patched for vote surfaces). */
  cardUrl: string | null;
  title: string;
  thumbUrl?: string | null;
  /** deterministic fixture payload; server-safe offline render (§2.5). */
  snapshot?: WeatherSnapshot;
  /** serving origin; defaults to window.location.origin on the client. */
  origin?: string;
  /** simulated API latency for interactive live mode. */
  delayMs?: number;
  /** parent concurrency gate; false keeps the frame as poster/shimmer only. */
  active?: boolean;
  /** IntersectionObserver lazy-mount; false mounts immediately when active. */
  lazy?: boolean;
  /** bump to force a fresh document (re-run card scripts) on control change. */
  generation?: number;
  /** center-fit instead of filling width (non-16:10 host box). */
  letterbox?: boolean;
  /** "content" zooms the pane onto the card's measured rect (gallery tiles). */
  fit?: "viewport" | "content";
  unavailableLabel?: string;
}) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [near, setNear] = useState(!lazy);
  const [html, setHtml] = useState<string | null>(null);
  const [error, setError] = useState(false);

  // lazy mount / offscreen unmount
  useEffect(() => {
    if (!lazy) {
      setNear(true);
      return;
    }
    const el = boxRef.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) setNear(e.isIntersecting);
      },
      { root: null, rootMargin: "400px 0px", threshold: 0 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [lazy]);

  const mounted = active && near;

  // fetch card.html once when it first needs to render
  useEffect(() => {
    if (!mounted || !cardUrl || html != null || error) return;
    let alive = true;
    fetchCard(cardUrl)
      .then((t) => {
        if (alive) setHtml(t);
      })
      .catch(() => {
        if (alive) setError(true);
      });
    return () => {
      alive = false;
    };
  }, [mounted, cardUrl, html, error]);

  const resolvedOrigin =
    origin ?? (typeof window !== "undefined" ? window.location.origin : undefined);
  const srcdoc =
    mounted && html != null
      ? buildSrcdoc(html, { origin: resolvedOrigin, snapshot, delayMs })
      : null;

  const showShimmer = mounted && !error && srcdoc == null;

  return (
    <div
      className="cardframe"
      ref={boxRef}
      style={{ aspectRatio: `${EXHIBIT_W} / ${EXHIBIT_H}` }}
    >
      {thumbUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img className="cf-poster" src={thumbUrl} alt="" aria-hidden loading="lazy" />
      ) : null}
      {showShimmer ? <div className="cf-shimmer" aria-hidden /> : null}
      {error || !cardUrl ? (
        <div className="cf-msg">{unavailableLabel}</div>
      ) : srcdoc != null ? (
        <ScaledExhibit
          srcdoc={srcdoc}
          title={title}
          frameKey={generation}
          letterbox={letterbox}
          fit={fit}
        />
      ) : null}
    </div>
  );
}
