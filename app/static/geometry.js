// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Schnuecks
// Aufbereitung der Vorschaudaten ohne three.js: läuft im Web Worker
// (geometry-worker.js) oder, falls der Browser das nicht kann, im Viewer selbst.

export const POLY_END = 2000; // Markierung im Helligkeitswert: Punkt beendet eine Bahn

// Binärdaten der Vorschau zerlegen (Aufbau siehe app/gcode_preview.py)
export function parsePreview(meta, buf) {
  const nPtsVals = meta.points * 2;
  const pts = new Uint16Array(buf, 0, nPtsVals);
  const polyOffset = Math.ceil((nPtsVals * 2) / 4) * 4;
  const polys = new Uint32Array(buf, polyOffset, meta.polys * 5);
  const metOffset = polyOffset + meta.polys * 5 * 4;
  const mets = buf.byteLength >= metOffset + meta.points * 8
    ? new Uint16Array(buf, metOffset, meta.points * 4)
    : new Uint16Array(meta.points * 4); // ältere Vorschau ohne Messwerte
  const trOffset = metOffset + meta.points * 8;
  const nTr = meta.travels || 0, nRe = meta.retracts || 0;
  const hasExtras = buf.byteLength >= trOffset + nTr * 10 + nRe * 6;
  const travels = hasExtras ? new Uint16Array(buf, trOffset, nTr * 5) : new Uint16Array(0);
  const retracts = hasExtras ? new Uint16Array(buf, trOffset + nTr * 10, nRe * 3) : new Uint16Array(0);
  return { pts, polys, mets, travels, retracts };
}

// Punkte, Attribute, Linien-Index, Schichtanfänge und Bahnlängen berechnen.
// progress(anteil) wird gelegentlich aufgerufen.
export function buildArrays(meta, pts, polys, progress = () => {}) {
  const nPts = pts.length / 2;
  const off = meta.offset, sc = meta.scale;
  const pos = new Float32Array(nPts * 3);
  const info = new Uint16Array(nPts * 4); // Schicht, Linientyp, Werkzeug, Helligkeit (+POLY_END)
  const cum = new Float64Array(nPts);     // Bahnlänge bis zum Punkt, Leerfahrten zählen nicht
  let nSeg = 0;
  for (let i = 0; i < polys.length; i += 5) nSeg += polys[i + 1] - 1;
  const index = new Uint32Array(nSeg * 2);
  const nL = meta.layers.length;
  // Wo jede Schicht beginnt: im Linien-Index und bei den Punkten (für die Röhren)
  const layerStart = new Uint32Array(nL + 1).fill(0xffffffff);
  const layerPoint = new Uint32Array(nL + 1).fill(0xffffffff);
  let k = 0;
  let acc = 0;
  const lx = 0.83, ly = 0.55; // Richtung des "Lichts" für etwas Struktur bei den Linien
  const step = Math.max(5, Math.floor(polys.length / 5 / 50) * 5);
  for (let i = 0; i < polys.length; i += 5) {
    if (i % step === 0) progress(i / polys.length);
    const start = polys[i], count = polys[i + 1], layer = polys[i + 2];
    const tool = polys[i + 3], feat = polys[i + 4];
    const z = meta.layers[layer] || 0;
    if (layerStart[layer] === 0xffffffff) layerStart[layer] = k;
    if (layerPoint[layer] === 0xffffffff) layerPoint[layer] = start;
    for (let j = 0; j < count; j++) {
      const p = start + j;
      const x = pts[p * 2] / sc - off, y = pts[p * 2 + 1] / sc - off;
      pos[p * 3] = x; pos[p * 3 + 1] = y; pos[p * 3 + 2] = z;
      if (j > 0) acc += Math.hypot(x - pos[p * 3 - 3], y - pos[p * 3 - 2]);
      cum[p] = acc;
      const q = j < count - 1 ? p + 1 : p - 1;
      const dx = pts[q * 2] - pts[p * 2], dy = pts[q * 2 + 1] - pts[p * 2 + 1];
      const len = Math.hypot(dx, dy) || 1;
      const shade = Math.round((0.68 + 0.32 * Math.abs((dx * lx + dy * ly) / len)) * 1000);
      info[p * 4] = layer; info[p * 4 + 1] = feat; info[p * 4 + 2] = tool;
      info[p * 4 + 3] = j < count - 1 ? shade : shade + POLY_END;
      if (j < count - 1) { index[k++] = p; index[k++] = p + 1; }
    }
  }
  const fillGaps = (arr, end) => {
    arr[nL] = end;
    for (let l = nL - 1; l >= 0; l--) if (arr[l] === 0xffffffff) arr[l] = arr[l + 1];
  };
  fillGaps(layerStart, k);
  fillGaps(layerPoint, nPts);
  progress(1);
  return { pos, info, cum, index, layerStart, layerPoint, nSeg };
}

// Alles in einem Schritt: laden, zerlegen, aufbauen
export async function loadPreview(api, progress = () => {}) {
  progress("load", 0);
  const [meta, buf] = await Promise.all([
    fetch(`${api}/meta`).then((r) => { if (!r.ok) throw new Error(r.statusText); return r.json(); }),
    fetch(`${api}/points`).then((r) => { if (!r.ok) throw new Error(r.statusText); return r.arrayBuffer(); }),
  ]);
  const parts = parsePreview(meta, buf);
  const arrays = buildArrays(meta, parts.pts, parts.polys, (v) => progress("build", v));
  return { meta, ...parts, ...arrays };
}
