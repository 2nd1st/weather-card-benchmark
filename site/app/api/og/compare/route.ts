// GET /api/og/compare?ids=a,b[,c,d]  or  ?share=<shareId>
// sharp composites 2-4 shot.webp side-by-side -> 1200x630 PNG (spec §4.9).
//   • labels ON for compare/model usage;
//   • labels OFF for ?share= (blind preserved in the preview);
//   • invalid/insufficient ids -> mosaic fallback;
//   • Cache-Control: public, max-age=3600.
import { NextRequest, NextResponse } from "next/server";
import fs from "node:fs";
import path from "node:path";
import sharp from "sharp";
import type { OverlayOptions } from "sharp";

import { listBatches, listConfigIds, loadSlots, loadConfig } from "@/lib/data";
import { slotDir } from "@/lib/paths";
import { configLabel } from "@/lib/label";
import { getShareRefs } from "@/lib/arena";
import type { Variant } from "@/lib/variant";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const W = 1200;
const H = 630;
const BG = { r: 11, g: 14, b: 20, alpha: 1 };

interface Shot {
  path: string;
  batchId: string;
  configId: string;
}

/** Find a valid slot's shot.webp for a configId (prefer P-q), scanning batches. */
function resolveShot(configId: string): Shot | null {
  for (const b of listBatches()) {
    let ids: string[];
    try {
      ids = listConfigIds(b.id);
    } catch {
      continue;
    }
    if (!ids.includes(configId)) continue;
    for (const v of ["P-q", "P-min"] as Variant[]) {
      let slots: Array<{ slot_index: number; state: string }>;
      try {
        slots = loadSlots(b.id, configId, v);
      } catch {
        continue;
      }
      const valid = slots.find((s) => s.state === "valid");
      if (valid) {
        const p = path.join(slotDir(b.id, configId, v, valid.slot_index), "shot.webp");
        if (fs.existsSync(p)) return { path: p, batchId: b.id, configId };
      }
    }
  }
  return null;
}

function labelFor(shot: Shot): string {
  try {
    const c = loadConfig(shot.batchId, shot.configId);
    return configLabel({ config_id: c.config_id, model_id: c.model_id, effort: c.effort, protocol: c.protocol });
  } catch {
    return shot.configId;
  }
}

function esc(s: string): string {
  return s.replace(/[<>&"']/g, (ch) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" }[ch]!));
}

function labelSvg(labels: string[], k: number): Buffer {
  const cellW = W / k;
  const parts: string[] = [`<svg width="${W}" height="${H}" xmlns="http://www.w3.org/2000/svg">`];
  for (let i = 0; i < k; i++) {
    const x = i * cellW;
    const cx = x + cellW / 2;
    parts.push(
      `<rect x="${x}" y="${H - 44}" width="${cellW}" height="44" fill="#0B0E14" fill-opacity="0.82"/>`,
    );
    parts.push(
      `<text x="${cx}" y="${H - 16}" font-family="ui-monospace,Menlo,monospace" font-size="20" fill="#E8ECF4" text-anchor="middle">${esc(labels[i] ?? "")}</text>`,
    );
  }
  // attribution line (in-pixel URL survives into wild screenshots, §4.9)
  parts.push(
    `<text x="20" y="34" font-family="ui-monospace,Menlo,monospace" font-size="18" fill="#93A0B8">weathercard.secondfirst.ai · same prompt · same data</text>`,
  );
  parts.push(`</svg>`);
  return Buffer.from(parts.join(""));
}

async function mosaicFallback(): Promise<NextResponse> {
  const p = path.join(process.cwd(), "public", "og", "mosaic.png");
  if (fs.existsSync(p)) {
    const buf = fs.readFileSync(p);
    return png(new Uint8Array(buf));
  }
  // generated dark placeholder with brand text
  const svg = Buffer.from(
    `<svg width="${W}" height="${H}" xmlns="http://www.w3.org/2000/svg">` +
      `<rect width="${W}" height="${H}" fill="#0B0E14"/>` +
      `<text x="${W / 2}" y="${H / 2}" font-family="ui-monospace,Menlo,monospace" font-size="42" fill="#37D6E4" text-anchor="middle">weathercard.secondfirst.ai</text>` +
      `<text x="${W / 2}" y="${H / 2 + 44}" font-family="ui-monospace,Menlo,monospace" font-size="22" fill="#93A0B8" text-anchor="middle">one prompt · every model · same data</text>` +
      `</svg>`,
  );
  const out = await sharp({ create: { width: W, height: H, channels: 4, background: BG } })
    .composite([{ input: svg, top: 0, left: 0 }])
    .png()
    .toBuffer();
  return png(new Uint8Array(out));
}

function png(body: Uint8Array): NextResponse {
  return new NextResponse(body as unknown as BodyInit, {
    headers: { "Content-Type": "image/png", "Cache-Control": "public, max-age=3600" },
  });
}

export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams;
  const shareId = sp.get("share");

  let configIds: string[] = [];
  let withLabels = true;

  if (shareId) {
    withLabels = false; // blind preserved in the share preview
    const refs = getShareRefs(shareId);
    if (refs) configIds = refs.map((r) => r.configId);
  } else {
    const idsRaw = sp.get("ids") || "";
    configIds = idsRaw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  }

  configIds = configIds.slice(0, 4);

  let shots: Shot[];
  try {
    shots = configIds
      .map((id) => resolveShot(id))
      .filter((s): s is Shot => s !== null);
  } catch {
    shots = [];
  }

  if (shots.length < 2) return mosaicFallback();

  try {
    const k = shots.length;
    const cellW = Math.floor(W / k);
    const layers: OverlayOptions[] = [];
    for (let i = 0; i < k; i++) {
      const buf = await sharp(shots[i].path)
        .resize(cellW, H, { fit: "inside", background: BG })
        .toBuffer();
      const meta = await sharp(buf).metadata();
      const iw = meta.width ?? cellW;
      const ih = meta.height ?? H;
      layers.push({
        input: buf,
        left: i * cellW + Math.max(0, Math.floor((cellW - iw) / 2)),
        top: Math.max(0, Math.floor((H - ih) / 2)),
      });
    }
    if (withLabels) {
      layers.push({ input: labelSvg(shots.map(labelFor), k), top: 0, left: 0 });
    }
    const out = await sharp({ create: { width: W, height: H, channels: 4, background: BG } })
      .composite(layers)
      .png()
      .toBuffer();
    return png(new Uint8Array(out));
  } catch {
    return mosaicFallback();
  }
}
