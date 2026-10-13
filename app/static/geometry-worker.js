// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Schnuecks
// Loads and processes the preview in a separate thread so the page stays
// responsive. The results are handed to the viewer without copying.

const version = new URL(self.location.href).searchParams.get("v") || "";
const ready = import(`/static/geometry.js?v=${version}`);

self.onmessage = async (e) => {
  try {
    const { loadPreview } = await ready;
    let last = 0;
    const data = await loadPreview(e.data.api, (phase, value) => {
      const now = performance.now();
      if (value >= 1 || now - last > 80) { last = now; self.postMessage({ type: "progress", phase, value }); }
    });
    const buffers = new Set();
    for (const v of Object.values(data)) if (ArrayBuffer.isView(v)) buffers.add(v.buffer);
    self.postMessage({ type: "done", data }, [...buffers]);
  } catch (err) {
    self.postMessage({ type: "error", message: err.message || String(err) });
  }
};
