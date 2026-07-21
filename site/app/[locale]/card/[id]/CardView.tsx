"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations, useFormatter, useLocale } from "next-intl";
import { Link } from "@/i18n/navigation";
import { buildSrcdoc, type WeatherSnapshot, type LiveParams } from "@/lib/live/inject";
import { ScaledExhibit } from "@/app/components/CardFrame";
import { CITIES, DELAY_PRESETS, searchCities } from "@/lib/cities";
import { ArmBadge, GradeBadge, EffortBadge, PatchedBadge, type Grade } from "@/app/components/Badge";
import { AttributionBar } from "@/app/components/AttributionBar";
import type { SlotState, L1, Fidelity, SlotTelemetry, Variant, Channel } from "@/lib/data";
import styles from "./card.module.css";

// Client view for /card/[id]. Model output is UNTRUSTED — the live render is a
// <iframe sandbox="allow-scripts" srcdoc> only (never inlined). Original is the
// default render; the patched toggle appears only for slots with a patch, and
// all metrics still refer to the original output (patch invariant #2).

export interface PatchInfo {
  category: string;
  reasonEn: string;
  reasonZh: string;
  patchedBy: string;
  ts: string;
  patchedUrl: string;
  diffUrl: string;
}
export interface CardSlot {
  variant: Variant;
  index: number;
  state: SlotState;
  shotUrl: string | null;
  thumbUrl: string | null;
  cardUrl: string | null; // ORIGINAL card.html public url
  highlightedSource: string | null;
  sourceLines: number | null;
  sourceBytes: number | null;
  l1: L1 | null;
  fidelity: Fidelity | null;
  telemetry: SlotTelemetry;
  patch: PatchInfo | null;
}
export interface LiveFixture {
  lat: string;
  lon: string;
  name: string;
  date: string;
}
export interface NeighborData {
  peerCount: number;
  hasSimilarity: boolean;
  variants: Record<string, Record<string, Array<{ configId: string; label: string; value: number }>>> | null;
}
export interface CardViewData {
  configId: string;
  label: string;
  family: string;
  modelId: string;
  effort: string | null;
  arm: string;
  grade: Grade;
  protocol: string;
  transport: string;
  billing: string;
  servedModel: string | null;
  batchId: string;
  batches: string[];
  capturedAt: string | null;
  N: number;
  slots: CardSlot[];
  snapshot: WeatherSnapshot | null;
  fixture: LiveFixture | null;
  neighbors: NeighborData;
  channels: Channel[];
  rawConfigHtml: string | null;
  rawConfigBytes: number | null;
  sendLogHtml: string | null;
  sendLogBytes: number | null;
  isAllFailed: boolean;
}

const keyOf = (s: { variant: Variant; index: number }) => `${s.variant}:${s.index}`;

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

export function CardView({ data }: { data: CardViewData }) {
  const t = useTranslations("card");
  const format = useFormatter();
  const locale = useLocale();
  const num = useMemo(() => new Intl.NumberFormat(locale), [locale]);

  const variantsPresent = useMemo(() => {
    const set = new Set(data.slots.map((s) => s.variant));
    return (["P-min", "P-q"] as Variant[]).filter((v) => set.has(v));
  }, [data.slots]);

  const [variant, setVariant] = useState<Variant>(
    variantsPresent.includes("P-q") ? "P-q" : variantsPresent[0] ?? "P-q",
  );

  const slotsForVariant = useMemo(
    () => data.slots.filter((s) => s.variant === variant),
    [data.slots, variant],
  );

  const initialSlot = useMemo(() => {
    const valid = slotsForVariant.find((s) => s.state === "valid");
    return valid ?? slotsForVariant[0] ?? null;
  }, [slotsForVariant]);

  const [selKey, setSelKey] = useState<string | null>(initialSlot ? keyOf(initialSlot) : null);
  // Keep the selection valid when the variant changes.
  useEffect(() => {
    if (!slotsForVariant.some((s) => keyOf(s) === selKey)) {
      setSelKey(initialSlot ? keyOf(initialSlot) : null);
    }
  }, [slotsForVariant, selKey, initialSlot]);

  const sel = slotsForVariant.find((s) => keyOf(s) === selKey) ?? initialSlot;

  const [renderMode, setRenderMode] = useState<"original" | "patched">("original");
  const [viewMode, setViewMode] = useState<"shot" | "live">("shot");
  const [liveCity, setLiveCity] = useState<LiveParams | null>(null); // null = fixture
  const [cityQuery, setCityQuery] = useState("");
  const [delayMs, setDelayMs] = useState(0);
  const [generation, setGeneration] = useState(0);

  const [html, setHtml] = useState<string | null>(null);
  const [liveErr, setLiveErr] = useState(false);

  // The URL that actually renders: patched when requested & available, else original.
  const activeCardUrl =
    sel && renderMode === "patched" && sel.patch ? sel.patch.patchedUrl : sel?.cardUrl ?? null;

  // Reset the live pane whenever the selection / render source changes.
  useEffect(() => {
    setHtml(null);
    setLiveErr(false);
    setViewMode(sel?.shotUrl ? "shot" : sel?.cardUrl ? "live" : "shot");
    setRenderMode("original");
  }, [selKey]);
  useEffect(() => {
    setHtml(null);
    setLiveErr(false);
  }, [activeCardUrl]);

  // Fetch the card html when live mode needs it.
  useEffect(() => {
    if (viewMode !== "live" || !activeCardUrl || html != null || liveErr) return;
    let alive = true;
    fetchCard(activeCardUrl)
      .then((tx) => alive && setHtml(tx))
      .catch(() => alive && setLiveErr(true));
    return () => {
      alive = false;
    };
  }, [viewMode, activeCardUrl, html, liveErr]);

  const srcdoc = useMemo(() => {
    if (html == null) return null;
    if (liveCity) {
      const origin = typeof window !== "undefined" ? window.location.origin : undefined;
      const date = data.fixture?.date ?? liveCity.date;
      return buildSrcdoc(html, { origin, params: { ...liveCity, date }, delayMs });
    }
    if (data.snapshot) {
      return buildSrcdoc(html, { snapshot: data.snapshot, delayMs });
    }
    const origin = typeof window !== "undefined" ? window.location.origin : undefined;
    return buildSrcdoc(html, { origin, delayMs });
  }, [html, liveCity, delayMs, data.snapshot, data.fixture]);

  // Whether the neighbor column has anything to show for the CURRENT variant
  // (mirrors <Neighbors> internal gating exactly): when false the exhibit takes
  // the full content width and the empty-note collapses to one inline line.
  const neighborsShowable = useMemo(() => {
    const map = data.neighbors.variants?.[variant] ?? null;
    return (
      data.neighbors.peerCount >= 6 &&
      data.neighbors.hasSimilarity &&
      !!map &&
      data.channels.some((c) => (map[c]?.length ?? 0) > 0)
    );
  }, [data.neighbors, data.channels, variant]);

  const fmtDate = (iso: string | null): string => {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return format.dateTime(d, { dateStyle: "medium", timeStyle: "short" });
  };

  return (
    <div className={`wrap ${styles.page}`}>
      <nav className={styles.crumb}>
        <Link href="/gallery">{t("breadcrumb.gallery")}</Link> / {data.configId}
      </nav>

      <div className={styles.head}>
        <div className={styles.titleCol}>
          <h1 className={styles.title}>
            {data.modelId}
            {data.effort ? <span className={styles.eff}> @ {data.effort}</span> : null}
            <span className={styles.arm}> · {data.arm}</span>
          </h1>
          <div className={styles.badges}>
            <ArmBadge arm={data.arm} />
            <GradeBadge grade={data.grade} unreviewed={data.grade === "community"} />
            {data.effort ? <EffortBadge effort={data.effort} /> : null}
            {sel?.patch ? <PatchedBadge /> : null}
          </div>

          <dl className={styles.prov}>
            <div>
              <dt className="k">{t("provenance.config")}</dt>
              <dd className="v">{data.configId}</dd>
              <CopyBtn value={data.configId} label={t("provenance.copy", { field: t("provenance.config") })} />
            </div>
            <div>
              <dt className="k">{t("provenance.batch")}</dt>
              <dd className="v">
                {data.batchId}
                {data.batches.length > 1 ? (
                  <span className="faint"> {t("provenance.batchesMore", { count: data.batches.length - 1 })}</span>
                ) : null}
              </dd>
              <CopyBtn value={data.batchId} label={t("provenance.copy", { field: t("provenance.batch") })} />
            </div>
            <div><dt className="k">{t("provenance.transport")}</dt><dd className="v">{data.transport}</dd></div>
            <div><dt className="k">{t("provenance.protocol")}</dt><dd className="v">{data.protocol} · {data.billing}</dd></div>
            {data.servedModel ? (
              <div>
                <dt className="k">{t("provenance.served")}</dt>
                <dd className="v">{data.servedModel}</dd>
                <CopyBtn value={data.servedModel} label={t("provenance.copy", { field: t("provenance.served") })} />
              </div>
            ) : null}
            <div><dt className="k">{t("provenance.captured")}</dt><dd className="v">{fmtDate(data.capturedAt)} · N={data.N}</dd></div>
          </dl>
          <div className={styles.provLink}>
            <Link href={`/model/${encodeURIComponent(data.modelId)}`}>{t("provenance.modelLink")}</Link>
          </div>
        </div>

        <div className={styles.controls}>
          {variantsPresent.length > 1 ? (
            <div className={styles.ctlRow}>
              <span className={styles.gl}>{t("controls.variant")}</span>
              <div className="seg">
                {variantsPresent.map((v) => (
                  <button key={v} className={v === variant ? "on" : ""} onClick={() => setVariant(v)}>{v}</button>
                ))}
              </div>
            </div>
          ) : null}

          {sel?.patch ? (
            <div className={styles.ctlRow}>
              <span className={styles.gl}>{t("controls.render")}</span>
              <div className="seg">
                <button className={renderMode === "original" ? "on" : ""} onClick={() => setRenderMode("original")}>
                  {t("controls.original")}
                </button>
                <button className={renderMode === "patched" ? "on" : ""} onClick={() => setRenderMode("patched")}>
                  {t("controls.patched")}
                </button>
              </div>
            </div>
          ) : null}

          <div className={styles.ctlRow}>
            <span className={styles.gl}>{t("controls.live")}</span>
            <input
              className={styles.sel}
              type="search"
              value={cityQuery}
              placeholder={t("controls.city")}
              aria-label={t("controls.city")}
              onChange={(e) => setCityQuery(e.target.value)}
              list="wcb-card-cities"
              style={{ minWidth: 130 }}
            />
            <datalist id="wcb-card-cities">
              {CITIES.map((c) => (<option key={c.name} value={c.name} />))}
            </datalist>
            <select
              className={styles.sel}
              value={delayMs}
              aria-label={t("controls.delay")}
              onChange={(e) => setDelayMs(Number(e.target.value))}
            >
              {DELAY_PRESETS.map((d) => (<option key={d.ms} value={d.ms}>{d.label}</option>))}
            </select>
            <button className={`${styles.mini} ${styles.iconBtn}`} onClick={() => { setViewMode("live"); setGeneration((g) => g + 1); }} title={t("controls.reload")}>↻</button>
          </div>
          {cityQuery ? (
            <div className={styles.ctlRow}>
              <span className={styles.gl} />
              <button
                className={`${styles.mini}${liveCity == null ? " " + styles.on : ""}`}
                onClick={() => { setLiveCity(null); setViewMode("live"); }}
              >
                {t("controls.fixture")}
              </button>
              {searchCities(cityQuery).slice(0, 5).map((c) => (
                <button
                  key={c.name}
                  className={`${styles.mini}${liveCity?.name === c.name ? " " + styles.on : ""}`}
                  onClick={() => { setLiveCity({ lat: c.lat, lon: c.lon, name: c.name, date: data.fixture?.date ?? "" }); setViewMode("live"); }}
                >
                  {c.name}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      </div>

      {sel?.patch ? (
        <div className={styles.patchNote}>
          <b>{t("patch.tag", { category: patchCategoryLabel(t, sel.patch.category) })}</b>
          <span>
            {locale === "zh" ? sel.patch.reasonZh : sel.patch.reasonEn} {t("patch.preserved")}{" "}
            <a href={sel.patch.diffUrl} target="_blank" rel="noreferrer">{t("patch.diff")}</a>
            <br />
            <span className="faint">{t("patch.by", { by: sel.patch.patchedBy, ts: fmtDate(sel.patch.ts) })}</span>
          </span>
        </div>
      ) : null}

      {/* slot chips */}
      <div className={styles.slotRail} role="tablist" aria-label="slots">
        {slotsForVariant.map((s) => {
          const cls = s.state === "valid" ? (s.patch ? "pat" : "ok") : "bad";
          const on = keyOf(s) === selKey;
          const stateLabel = s.state === "valid"
            ? (s.patch ? t("slots.patched") : t("slots.valid"))
            : t("slots.noOutput");
          return (
            <button
              key={keyOf(s)}
              className={`${styles.schip} ${styles[cls]}${on ? " " + styles.on : ""}`}
              aria-pressed={on}
              onClick={() => setSelKey(keyOf(s))}
            >
              <i />{t("slots.slot", { index: s.index })} · {stateLabel}
            </button>
          );
        })}
      </div>

      {/* stage: exhibit + neighbors */}
      <div className={`${styles.stage}${neighborsShowable ? "" : " " + styles.stageSolo}`}>
        <div className={`${styles.stageFrame} panelbox exhibit`}>
          <div className="halo" />
          <div className={styles.viewerHead}>
            <span>{sel ? t("viewer.frameLabel", { index: sel.index, variant }) : ""}</span>
            <span className={styles.modeTabs}>
              <button
                className={`${styles.mini}${viewMode === "shot" ? " " + styles.on : ""}`}
                onClick={() => setViewMode("shot")}
                disabled={!sel?.shotUrl}
              >
                {t("controls.screenshot")}
              </button>
              <button
                className={`${styles.mini}${viewMode === "live" ? " " + styles.on : ""}`}
                onClick={() => setViewMode("live")}
                disabled={!activeCardUrl}
                title={t("viewer.runLivePrompt")}
              >
                {t("controls.runLive")}
              </button>
            </span>
          </div>

          <div className={styles.viewerBox}>
            {viewMode === "shot" ? (
              sel?.shotUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img className={styles.viewerImg} src={sel.shotUrl} alt={t("viewer.shotAlt", { label: data.label, variant, index: sel.index })} />
              ) : (
                <div className={styles.viewerEmpty}>
                  <span>{t("viewer.noShot")}</span>
                  {activeCardUrl ? (
                    <button className={styles.mini} onClick={() => setViewMode("live")}>{t("controls.runLive")}</button>
                  ) : null}
                </div>
              )
            ) : liveErr || !activeCardUrl ? (
              <div className={styles.viewerEmpty}>{t("viewer.unavailable")}</div>
            ) : srcdoc == null ? (
              <div className={styles.viewerEmpty}>{t("viewer.loading")}</div>
            ) : (
              // Live render at the measured 1280×800 viewport (see CardFrame /
              // ScaledExhibit — runner VIEWPORT parity), so it is visually
              // swappable in place with the 1280×800 screenshot mode above.
              // fit STAYS "viewport" (default) here: it sits beside the 1280×800
              // screenshot and MUST match its framing exactly — no content zoom.
              <ScaledExhibit
                srcdoc={srcdoc}
                title={sel ? t("viewer.frameLabel", { index: sel.index, variant }) : "live card"}
                frameKey={generation}
                iframeBackground="#fff"
              />
            )}
          </div>

          {viewMode === "live" && activeCardUrl ? (
            <div className={styles.liveRow}>
              <span className={styles.scenNote}>
                {liveCity
                  ? t("viewer.liveNote", { city: liveCity.name })
                  : data.fixture
                    ? t("viewer.fixtureNote", { city: data.fixture.name, date: data.fixture.date })
                    : ""}
              </span>
            </div>
          ) : null}

          {sel?.patch ? <p className={styles.metricsCaption}>{t("patch.metricsCaption")}</p> : null}
          <p className={styles.caption}>{t("viewer.caption")}</p>
          <AttributionBar />
        </div>

        {neighborsShowable ? (
          <Neighbors data={data} variant={variant} />
        ) : (
          <p className={styles.neighInline}>
            <span className={styles.neighInlineLabel}>{t("neighbors.heading")}</span>
            {data.neighbors.peerCount < 6 ? t("neighbors.tooFewPeers") : t("neighbors.noneInSession")}
          </p>
        )}
      </div>

      {data.isAllFailed ? (
        <div className={styles.tombstone}>
          <h3>{t("tombstone.heading")}</h3>
          <p>{t("tombstone.message")}</p>
        </div>
      ) : null}

      {/* fidelity */}
      <section className={styles.sec}>
        <div className={styles.secHead}><h2 className={styles.secH}>{t("fidelity.heading")}</h2></div>
        {sel?.fidelity ? (
          <FidelityBlock f={sel.fidelity} t={t} />
        ) : (
          <p className={styles.note}>{t("fidelity.none", { state: sel?.state ?? "—" })}</p>
        )}
      </section>

      {/* source */}
      <section className={styles.sec}>
        <div className={styles.dlRow}>
          <h2 className={styles.secH}>{t("source.heading")}</h2>
          {activeCardUrl ? (
            <a className={styles.mini} href={activeCardUrl} download={`${data.configId}--${variant}--slot${sel?.index}.html`}>
              {t("source.download")}
            </a>
          ) : null}
        </div>
        {sel?.highlightedSource ? (
          <SourceBlock html={sel.highlightedSource} lines={sel.sourceLines} bytes={sel.sourceBytes} t={t} num={num} />
        ) : (
          <p className={styles.note}>{t("source.none", { state: sel?.state ?? "—" })}</p>
        )}
      </section>

      {/* L1 */}
      <section className={styles.sec}>
        <div className={styles.secHead}><h2 className={styles.secH}>{t("l1.heading")}</h2></div>
        {sel?.l1 ? <L1Block l1={sel.l1} t={t} num={num} /> : <p className={styles.note}>{t("l1.none", { state: sel?.state ?? "—" })}</p>}
      </section>

      {/* slot telemetry */}
      <section className={styles.sec}>
        <div className={styles.secHead}><h2 className={styles.secH}>{t("telemetry.heading")}</h2></div>
        {sel ? (
          <div className={styles.scrollX}>
            <table className={styles.tbl}>
              <tbody>
                <tr>
                  <th>{t("telemetry.promptTokens")}</th><td>{num.format(sel.telemetry.prompt_tokens)}</td>
                  <th>{t("telemetry.completionTokens")}</th><td>{num.format(sel.telemetry.completion_tokens)}</td>
                </tr>
                <tr>
                  <th>{t("telemetry.totalTokens")}</th><td>{num.format(sel.telemetry.total_tokens)}</td>
                  <th>{t("telemetry.wall")}</th><td>{fmtMs(sel.telemetry.wall_ms, num)}</td>
                </tr>
                <tr>
                  <th>{t("telemetry.cost")}</th><td>{sel.telemetry.cost_usd == null ? "—" : `$${sel.telemetry.cost_usd}`}</td>
                  <th>{t("telemetry.requestId")}</th><td>{sel.telemetry.request_id ?? "—"}</td>
                </tr>
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      {/* raw config + send-log */}
      <section className={styles.sec}>
        <details className={styles.collapse}>
          <summary className={styles.collapseSummary}>
            <span className={styles.collapseLabel}>{t("rawConfig.heading")}</span>
            <span className={styles.collapseMeta}>
              {data.rawConfigBytes != null ? fmtBytes(data.rawConfigBytes, num) : null}
              <span className={styles.chev} aria-hidden>▾</span>
            </span>
          </summary>
          <div className={styles.collapseBody}>
            {data.rawConfigHtml ? (
              <div className={`${styles.shiki} ${styles.scrollX}`} dangerouslySetInnerHTML={{ __html: data.rawConfigHtml }} />
            ) : null}
          </div>
        </details>
      </section>
      <section className={styles.sec}>
        <details className={styles.collapse}>
          <summary className={styles.collapseSummary}>
            <span className={styles.collapseLabel}>{t("sendLog.heading")}</span>
            <span className={styles.collapseMeta}>
              {data.sendLogHtml && data.sendLogBytes != null
                ? fmtBytes(data.sendLogBytes, num)
                : data.sendLogHtml == null
                  ? t("sendLog.notOnDisk")
                  : null}
              <span className={styles.chev} aria-hidden>▾</span>
            </span>
          </summary>
          <div className={styles.collapseBody}>
            {data.sendLogHtml ? (
              <div className={`${styles.shiki} ${styles.scrollX}`} dangerouslySetInnerHTML={{ __html: data.sendLogHtml }} />
            ) : (
              <p className={styles.note}>{t("sendLog.none")}</p>
            )}
          </div>
        </details>
      </section>
    </div>
  );
}

function Neighbors({ data, variant }: { data: CardViewData; variant: Variant }) {
  const t = useTranslations("card");
  const map = data.neighbors.variants?.[variant] ?? null;
  const channelsWithData = useMemo(
    () => (map ? data.channels.filter((c) => (map[c]?.length ?? 0) > 0) : []),
    [map, data.channels],
  );
  const [channel, setChannel] = useState<Channel | null>(null);
  const active = channel && channelsWithData.includes(channel) ? channel : channelsWithData[0] ?? null;

  const list = active && map ? map[active] ?? [] : [];
  const nearest = list.slice(0, 5);
  const farthest = list.length > 5 ? list.slice(-5).reverse() : [];

  const tooFew = data.neighbors.peerCount < 6;

  return (
    <div className={styles.neigh}>
      <h4>{t("neighbors.heading")}</h4>
      {tooFew ? (
        <p className={styles.neighNote}>{t("neighbors.tooFewPeers")}</p>
      ) : !data.neighbors.hasSimilarity || channelsWithData.length === 0 ? (
        <p className={styles.neighNote}>{t("neighbors.noneInSession")}</p>
      ) : (
        <>
          <div className={styles.chanStrip}>
            {channelsWithData.map((c) => (
              <button
                key={c}
                className={`${styles.chanChip}${c === active ? " " + styles.on : ""}`}
                onClick={() => setChannel(c)}
              >
                {c}
              </button>
            ))}
          </div>
          {active ? (
            <>
              <div className={styles.nGroup}>
                <div className={styles.nGroupLabel}>{t("neighbors.nearest")}</div>
                {nearest.map((n) => (
                  <Link key={n.configId} href={`/card/${encodeURIComponent(n.configId)}`} className={styles.nrow}>
                    <span className="nl">{n.label}</span>
                    <span className="nv">{n.value.toFixed(2)}</span>
                  </Link>
                ))}
              </div>
              {farthest.length > 0 ? (
                <div className={styles.nGroup}>
                  <div className={styles.nGroupLabel}>{t("neighbors.farthest")}</div>
                  {farthest.map((n) => (
                    <Link key={n.configId} href={`/card/${encodeURIComponent(n.configId)}`} className={`${styles.nrow} ${styles.far}`}>
                      <span className="nl">{n.label}</span>
                      <span className="nv">{n.value.toFixed(2)}</span>
                    </Link>
                  ))}
                </div>
              ) : null}
              <p className={styles.neighNote}>{t("neighbors.session", { batch: data.batchId })}</p>
              <p className={styles.neighNote}>{t("neighbors.caption")}</p>
            </>
          ) : null}
        </>
      )}
    </div>
  );
}

function FidelityBlock({ f, t }: { f: Fidelity; t: ReturnType<typeof useTranslations> }) {
  const rows: Array<{ label: string; state: string; reason: string | null; extra?: string }> = [
    { label: t("fidelity.name"), state: f.name.state, reason: f.name.reason },
    { label: t("fidelity.condition"), state: f.condition.state, reason: f.condition.reason },
    { label: t("fidelity.date"), state: f.date.state, reason: f.date.reason },
    { label: t("fidelity.maxTemp"), state: f.max.state, reason: f.max.reason, extra: f.max.value != null ? t("fidelity.shown", { value: f.max.value }) : undefined },
    { label: t("fidelity.minTemp"), state: f.min.state, reason: f.min.reason, extra: f.min.value != null ? t("fidelity.shown", { value: f.min.value }) : undefined },
  ];
  return (
    <>
      <ul className={styles.fidList}>
        {rows.map((r) => (
          <li key={r.label} className={styles.fidItem}>
            <span className={styles.fidField}>{r.label}</span>
            <FieldState state={r.state} />
            {r.extra ? <span className={styles.fidObs}>{r.extra}</span> : null}
            {r.reason ? <span className={styles.fidReason}>{r.reason}</span> : null}
          </li>
        ))}
        <li className={styles.fidItem}>
          <span className={styles.fidField}>{t("fidelity.hourly")}</span>
          <FieldState state={f.hourly.state} />
          <span className={styles.fidObs}>
            {t("fidelity.hourlyDetail", {
              coverage: f.hourly.coverage,
              match: f.hourly.points_match,
              mismatch: f.hourly.points_mismatch,
              notShown: f.hourly.points_not_shown,
            })}
          </span>
        </li>
        <li className={styles.fidItem}>
          <span className={styles.fidField}>{t("fidelity.requestSide")}</span>
          <FieldState state={f.request_side.status ?? "not-found"} />
          <span className={styles.fidObs}>{t("fidelity.violations", { count: f.request_side.violations.length })}</span>
        </li>
      </ul>
      <p className={styles.noteFaint}>{t("fidelity.extractor", { version: f.extractor_version })}</p>
    </>
  );
}

function FieldState({ state }: { state: string }) {
  const ok = state === "match";
  const cls = ok ? styles.stateOk : styles.stateFail;
  return (
    <span className={`${styles.stateBadge} ${cls}`}>
      <span className={styles.stateIcon} aria-hidden>{ok ? "✓" : "✕"}</span>
      {state}
    </span>
  );
}

function L1Block({ l1, t, num }: { l1: L1; t: ReturnType<typeof useTranslations>; num: Intl.NumberFormat }) {
  return (
    <div className={styles.scrollX}>
      <table className={styles.tbl}>
        <tbody>
          <tr>
            <th>{t("l1.bytesTotal")}</th><td>{num.format(l1.bytes.total)}</td>
            <th>{t("l1.bytesSplit")}</th><td>{num.format(l1.bytes.html)} / {num.format(l1.bytes.css)} / {num.format(l1.bytes.js)}</td>
          </tr>
          <tr>
            <th>{t("l1.domNodes")}</th><td>{num.format(l1.structure.dom_nodes)}</td>
            <th>{t("l1.domDepth")}</th><td>{l1.structure.dom_depth}</td>
          </tr>
          <tr>
            <th>{t("l1.cssRules")}</th><td>{l1.structure.css_rules}</td>
            <th>{t("l1.jsPresent")}</th><td>{l1.structure.js_bytes_present ? t("l1.yes") : t("l1.no")}</td>
          </tr>
          <tr>
            <th>{t("l1.brightness")}</th><td>{l1.visual.brightness.toFixed(1)}</td>
            <th>{t("l1.contrast")}</th><td>{l1.visual.contrast.toFixed(1)}</td>
          </tr>
          <tr>
            <th>{t("l1.colorfulness")}</th><td>{l1.visual.colorfulness.toFixed(1)}</td>
            <th>{t("l1.whitespace")}</th><td>{(l1.visual.whitespace_ratio * 100).toFixed(1)}%</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

const KNOWN_PATCH_CATEGORIES = new Set(["render-fix", "asset-path", "truncation-repair"]);
function patchCategoryLabel(t: ReturnType<typeof useTranslations>, category: string): string {
  return KNOWN_PATCH_CATEGORIES.has(category) ? t(`patch.category.${category}`) : category;
}

function fmtMs(ms: number | null | undefined, num: Intl.NumberFormat): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${num.format(ms)} ms`;
  return `${num.format(Number((ms / 1000).toFixed(1)))} s`;
}

function fmtBytes(bytes: number | null | undefined, num: Intl.NumberFormat): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${num.format(bytes)} B`;
  if (bytes < 1024 * 1024) return `${num.format(Number((bytes / 1024).toFixed(1)))} KB`;
  return `${num.format(Number((bytes / (1024 * 1024)).toFixed(1)))} MB`;
}

// Bounded source viewer (spec: never a raw mid-line clip). Collapsed to a fixed
// height with a bottom fade + an explicit expand control carrying the line count
// and byte size; expanded to a taller scroll region.
function SourceBlock({
  html,
  lines,
  bytes,
  t,
  num,
}: {
  html: string;
  lines: number | null;
  bytes: number | null;
  t: ReturnType<typeof useTranslations>;
  num: Intl.NumberFormat;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className={`${styles.sourcePanel} ${expanded ? styles.sourceOpen : styles.sourceClosed}`}>
      <div className={`${styles.shiki} ${styles.scrollX}`} dangerouslySetInnerHTML={{ __html: html }} />
      {!expanded ? <div className={styles.sourceFade} aria-hidden /> : null}
      <button
        type="button"
        className={styles.sourceToggle}
        aria-expanded={expanded}
        onClick={() => setExpanded((e) => !e)}
      >
        <span>
          {expanded
            ? t("source.collapse")
            : t("source.expand", { lines: lines != null ? num.format(lines) : "—", size: fmtBytes(bytes, num) })}
        </span>
        <span className={styles.sourceToggleChev} aria-hidden>{expanded ? "▴" : "▾"}</span>
      </button>
    </div>
  );
}

// Copy-to-clipboard affordance for provenance identifiers.
function CopyBtn({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className={styles.copyBtn}
      aria-label={label}
      title={label}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        } catch {
          /* clipboard unavailable — no-op */
        }
      }}
    >
      <span aria-hidden>{copied ? "✓" : "⧉"}</span>
    </button>
  );
}
