"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useLocale, useTranslations } from "next-intl";

import { GateOverlay } from "@/app/components/GateOverlay";

// The arena gate as it applies to BARE /gallery (§4.0): an OVERLAY above the
// already-SSR'd grid, never a redirect. Visibility is CSS-driven by the layout's
// pre-hydration <html data-gate> stamp; this component only runs its blind-vote
// flow when data-gate === "pending" (fresh visitor) and dismisses itself on the
// final vote or an explicit skip. It talks to the shared arena API (source=gate)
// so gate votes land in SQLite labeled coerced-unlock and excluded from default
// aggregation (§3.2). Cards arrive as ready-to-srcdoc blind HTML — no identity
// data reaches the client before a vote is recorded.

const VOTES = Number(process.env.NEXT_PUBLIC_WCB_GATE_VOTES) || 3;
const GATE_KEY = "wcb.gate.v1";
const VOTER_KEY = "wcb.voter.v1";

type Choice = "a" | "b" | "tie" | "both_bad";

interface IssuedPair {
  token: string;
  angle: string;
  variant: string;
  city: string;
  date: string;
  cards: { html: string }[];
}

function getVoterId(): string {
  try {
    let id = localStorage.getItem(VOTER_KEY);
    if (!id) {
      id =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : `v-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      localStorage.setItem(VOTER_KEY, id);
    }
    return id;
  } catch {
    return `v-${Date.now()}`;
  }
}

export function GalleryGate() {
  const t = useTranslations("gallery");
  const locale = useLocale();

  // Server + first client render produce nothing (no hydration mismatch); the
  // mount effect decides whether the gate is live from the data-gate stamp.
  const [active, setActive] = useState(false);
  const [pair, setPair] = useState<IssuedPair | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [loaded, setLoaded] = useState<[boolean, boolean]>([false, false]);
  const [done, setDone] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [finished, setFinished] = useState(false);

  const issuedAt = useRef(0);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  useEffect(() => {
    try {
      if (document.documentElement.getAttribute("data-gate") === "pending") {
        setActive(true);
      }
    } catch {
      /* fail open — no gate */
    }
  }, []);

  const fetchPair = useCallback(async () => {
    setLoading(true);
    setError(false);
    setLoaded([false, false]);
    try {
      const res = await fetch("/api/arena/pair?source=gate&angle=overall", {
        cache: "no-store",
      });
      if (!res.ok) throw new Error(`pair ${res.status}`);
      const data = (await res.json()) as IssuedPair;
      if (!aliveRef.current) return;
      if (!Array.isArray(data.cards) || data.cards.length < 2) {
        throw new Error("bad pair");
      }
      issuedAt.current = Date.now();
      setPair(data);
    } catch {
      if (aliveRef.current) setError(true);
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (active && !pair && !error) void fetchPair();
  }, [active, pair, error, fetchPair]);

  const complete = useCallback(() => {
    try {
      localStorage.setItem(GATE_KEY, String(Date.now()));
      document.documentElement.setAttribute("data-gate", "done");
    } catch {
      /* ignore */
    }
    setFinished(true);
  }, []);

  const markLoaded = useCallback((i: number) => {
    setLoaded((prev) => {
      if (prev[i]) return prev;
      const next: [boolean, boolean] = [prev[0], prev[1]];
      next[i] = true;
      return next;
    });
  }, []);

  const vote = useCallback(
    async (choice: Choice) => {
      if (!pair || submitting) return;
      setSubmitting(true);
      try {
        const res = await fetch("/api/vote", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            token: pair.token,
            choice,
            latencyMs: Date.now() - issuedAt.current,
            voterId: getVoterId(),
            locale,
          }),
        });
        if (!aliveRef.current) return;
        if (res.ok) {
          const next = done + 1;
          if (next >= VOTES) {
            complete();
          } else {
            setDone(next);
            setPair(null); // triggers the next fetch
          }
        } else {
          // 410 (too-fast / used-or-expired) or 429 — forgive, hand a fresh pair.
          setPair(null);
        }
      } catch {
        if (aliveRef.current) setPair(null);
      } finally {
        if (aliveRef.current) setSubmitting(false);
      }
    },
    [pair, submitting, done, locale, complete],
  );

  const skip = useCallback(() => {
    // Records nothing; still sets the gate done and reveals the grid (§4.0).
    complete();
  }, [complete]);

  if (!active) return null;

  const bothLoaded = loaded[0] && loaded[1];
  const disabled = !pair || !bothLoaded || submitting;

  return (
    <GateOverlay
      title={t("gate.title")}
      subtitle={t("gate.subtitle")}
      done={done}
      total={VOTES}
      meta={
        pair ? (
          <>
            <span>
              <b>{pair.city}</b> · {pair.date}
            </span>
            <span>
              {pair.angle} · {pair.variant}
            </span>
          </>
        ) : undefined
      }
      skipLabel={t("gate.skip")}
      onSkip={skip}
      canSkip={pair != null || error}
      forceHidden={finished}
    >
      {error ? (
        <div
          className="gate-status"
          style={{ display: "flex", flexDirection: "column", gap: 12, alignItems: "center" }}
        >
          <p className="sec-sub">{t("gate.error")}</p>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => void fetchPair()}
          >
            {t("gate.retry")}
          </button>
        </div>
      ) : !pair ? (
        <p className="sec-sub gate-status">{t("gate.loading")}</p>
      ) : (
        <>
          <div className="pair">
            {pair.cards.slice(0, 2).map((c, i) => (
              <div className="panelbox blindframe" key={`${done}-${i}`}>
                <div className="blind-tag">
                  <span className="who">
                    {i === 0 ? t("gate.cardA") : t("gate.cardB")}
                  </span>
                </div>
                <BlindFrame
                  html={c.html}
                  title={i === 0 ? t("gate.cardA") : t("gate.cardB")}
                  onLoaded={() => markLoaded(i)}
                />
              </div>
            ))}
          </div>
          <div className="choices">
            <button
              type="button"
              className="choice"
              disabled={disabled}
              onClick={() => vote("a")}
            >
              {t("gate.choose.a")}
            </button>
            <button
              type="button"
              className="choice"
              disabled={disabled}
              onClick={() => vote("b")}
            >
              {t("gate.choose.b")}
            </button>
            <button
              type="button"
              className="choice mute"
              disabled={disabled}
              onClick={() => vote("tie")}
            >
              {t("gate.choose.tie")}
            </button>
            <button
              type="button"
              className="choice mute"
              disabled={disabled}
              onClick={() => vote("both_bad")}
            >
              {t("gate.choose.bothBad")}
            </button>
          </div>
          {!bothLoaded ? (
            <p className="sec-sub gate-status">{t("gate.waitLoad")}</p>
          ) : null}
        </>
      )}
    </GateOverlay>
  );
}

// A blind inline-HTML exhibit frame. The card doc is served ready-to-srcdoc by
// the arena API (base/stub already injected server-side), so it renders as-is
// inside sandbox="allow-scripts" (no allow-same-origin → opaque origin, model
// output stays untrusted, non-neg #3). The 1280×800 native frame is scaled to
// the container width; onLoaded gates the choice buttons (both sides must render
// before a vote is allowed — fairness + a natural dwell floor).
function BlindFrame({
  html,
  title,
  onLoaded,
}: {
  html: string;
  title: string;
  onLoaded: () => void;
}) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(0);

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const measure = () => setScale(el.clientWidth / 1280);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return (
    <div className="cardframe" ref={boxRef} style={{ aspectRatio: "1280 / 800" }}>
      {scale > 0 ? (
        <iframe
          title={title}
          sandbox="allow-scripts"
          srcDoc={html}
          onLoad={onLoaded}
          style={{
            width: "1280px",
            height: "800px",
            transform: `scale(${scale})`,
            transformOrigin: "top left",
          }}
        />
      ) : null}
    </div>
  );
}
