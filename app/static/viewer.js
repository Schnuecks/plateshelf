// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Schnuecks
import * as THREE from "three";
import { OrbitControls } from "/static/vendor/OrbitControls.js";

const root = document.getElementById("viewer");
const fid = root.dataset.fid;
const plate = root.dataset.plate;
const api = `/api/preview/${fid}/${plate}`;
const filaments = JSON.parse(document.getElementById("viewer-filaments").textContent);

const loading = document.getElementById("viewer-loading");
const loadingText = document.getElementById("viewer-loading-text");
const loadingBar = document.getElementById("viewer-loading-bar");
const panel = document.getElementById("viewer-panel");

// Farben der Linientypen, angelehnt an die Slicer-Vorschau
const FEATURE_COLORS = {
  "Außenwand": "#FF7D38", "Innenwand": "#FFD37A", "Überhangwand": "#3D6BFF",
  "Infill": "#B8352E", "Massives Infill": "#C23BC9", "Oberfläche": "#FF3040",
  "Unterseite": "#7B61E8", "Brücke": "#4DA6FF", "Lückenfüllung": "#F2F2F2",
  "Schwebende Hülle": "#5CBF6A", "Stützen": "#3CB371", "Stützen-Kontakt": "#1F7A4D",
  "Stützen-Übergang": "#2E9E6B", "Reinigungsturm": "#9AA1A8", "Brim": "#00A8A8",
  "Skirt": "#00A8A8", "Glätten": "#FF99CC", "Sonstiges": "#7F868C",
};

const FEATURE_ORDER = Object.keys(FEATURE_COLORS);
const featureRank = (name) => {
  const i = FEATURE_ORDER.indexOf(name);
  return name === "Sonstiges" ? 999 : i < 0 ? 900 : i;
};

const DEFAULT_UNI = "#2FB36B"; // kräftiges Grün für die einfarbige Ansicht
const MODES = { tool: 0, feature: 1, uni: 2 };
const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* privat */ } },
};
const IDLE_MS = 180;          // so lange nach der letzten Bewegung wieder volle Qualität
const TARGET_MS = 22;         // Bildzeit, die beim Drehen noch als flüssig gilt
const REST_MAX_MS = 400;      // länger darf ein einzelnes Standbild nicht dauern
const POLY_END = 2000;        // Markierung im Helligkeitswert: Punkt beendet eine Bahn

// Plattenwechsel mit Pfeiltasten, schon während die Vorschau noch lädt
window.addEventListener("keydown", (e) => {
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
  if (e.target.tagName === "INPUT" && e.target.type !== "range") return;
  const link = document.querySelector(`[data-pager="${e.key === "ArrowLeft" ? "prev" : "next"}"]`);
  if (link) { e.preventDefault(); location.href = link.href; }
});

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitForPreview() {
  for (;;) {
    const res = await fetch(api);
    if (!res.ok) throw new Error("Vorschau nicht verfügbar");
    const st = await res.json();
    if (st.state === "ready") return;
    if (st.state === "error") throw new Error(st.error || "Aufbereitung fehlgeschlagen");
    loadingText.textContent = `G-Code wird aufbereitet: ${Math.round((st.progress || 0) * 100)} %`;
    loadingBar.value = (st.progress || 0) * 100;
    await sleep(1000);
  }
}

async function loadData() {
  loadingText.textContent = "Druckbahnen werden geladen";
  loadingBar.removeAttribute("value");
  const [meta, buf] = await Promise.all([
    fetch(`${api}/meta`).then((r) => r.json()),
    fetch(`${api}/points`).then((r) => r.arrayBuffer()),
  ]);
  const nPtsVals = meta.points * 2;
  const pts = new Uint16Array(buf, 0, nPtsVals);
  const polyOffset = Math.ceil((nPtsVals * 2) / 4) * 4;
  const polys = new Uint32Array(buf, polyOffset, meta.polys * 5);
  return { meta, pts, polys };
}

function buildGeometry(meta, pts, polys) {
  const nPts = pts.length / 2;
  const off = meta.offset, sc = meta.scale;
  const pos = new Float32Array(nPts * 3);
  const info = new Uint16Array(nPts * 4); // Schicht, Linientyp, Werkzeug, Helligkeit (+POLY_END)
  let nSeg = 0;
  for (let i = 0; i < polys.length; i += 5) nSeg += polys[i + 1] - 1;
  const index = new Uint32Array(nSeg * 2);
  const nL = meta.layers.length;
  // Wo jede Schicht beginnt: im Linien-Index und bei den Punkten (für die Röhren)
  const layerStart = new Uint32Array(nL + 1).fill(0xffffffff);
  const layerPoint = new Uint32Array(nL + 1).fill(0xffffffff);
  let k = 0;
  const lx = 0.83, ly = 0.55; // Richtung des "Lichts" für etwas Struktur bei den Linien
  for (let i = 0; i < polys.length; i += 5) {
    const start = polys[i], count = polys[i + 1], layer = polys[i + 2];
    const tool = polys[i + 3], feat = polys[i + 4];
    const z = meta.layers[layer] || 0;
    if (layerStart[layer] === 0xffffffff) layerStart[layer] = k;
    if (layerPoint[layer] === 0xffffffff) layerPoint[layer] = start;
    for (let j = 0; j < count; j++) {
      const p = start + j;
      const x = pts[p * 2] / sc - off, y = pts[p * 2 + 1] / sc - off;
      pos[p * 3] = x; pos[p * 3 + 1] = y; pos[p * 3 + 2] = z;
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

  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("info", new THREE.BufferAttribute(info, 4));
  geo.setIndex(new THREE.BufferAttribute(index, 1));
  geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1e6);
  geo.userData = { layerStart, nSeg };

  // Röhren: ein Rechteck pro Bahnstück, als Instanzen über dieselben Punktdaten.
  // Instanz i verbindet Punkt i mit Punkt i+1; Bahnenden sind markiert und entfallen.
  // Eckpunkte eines Rechtecks: x = Position entlang der Bahn (0..1), y = Seite (-1..1)
  const quad = new Float32Array([0, -1, 0, 1, -1, 0, 1, 1, 0, 0, -1, 0, 1, 1, 0, 0, 1, 0]);
  const tube = new THREE.InstancedBufferGeometry();
  tube.setAttribute("position", new THREE.BufferAttribute(quad, 3));
  const posBuf = new THREE.InstancedInterleavedBuffer(pos, 3, 1);
  tube.setAttribute("aStart", new THREE.InterleavedBufferAttribute(posBuf, 3, 0));
  tube.setAttribute("aEnd", new THREE.InterleavedBufferAttribute(posBuf, 3, 3));
  const infoBuf = new THREE.InstancedInterleavedBuffer(info, 4, 1);
  tube.setAttribute("aInfo", new THREE.InterleavedBufferAttribute(infoBuf, 4, 0));
  tube.instanceCount = Math.max(0, nPts - 1);
  tube.boundingSphere = geo.boundingSphere;
  tube.userData = { layerPoint, nPts };

  return { geo, tube, pos, info };
}

// Ausgedünnte Linien: nur jede step-te Schicht, gleiche Punktdaten
function buildLod(meta, polys, geo, step) {
  const nL = meta.layers.length;
  let n = 0;
  for (let i = 0; i < polys.length; i += 5) if (polys[i + 2] % step === 0) n += polys[i + 1] - 1;
  const index = new Uint32Array(n * 2);
  const layerStart = new Uint32Array(nL + 1).fill(0xffffffff);
  let k = 0;
  for (let i = 0; i < polys.length; i += 5) {
    const start = polys[i], count = polys[i + 1], layer = polys[i + 2];
    if (layerStart[layer] === 0xffffffff) layerStart[layer] = k;
    if (layer % step !== 0) continue;
    for (let j = 0; j < count - 1; j++) { index[k++] = start + j; index[k++] = start + j + 1; }
  }
  layerStart[nL] = k;
  for (let l = nL - 1; l >= 0; l--) if (layerStart[l] === 0xffffffff) layerStart[l] = layerStart[l + 1];
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", geo.getAttribute("position"));
  g.setAttribute("info", geo.getAttribute("info"));
  g.setIndex(new THREE.BufferAttribute(index, 1));
  g.boundingSphere = geo.boundingSphere;
  g.userData = { layerStart, nSeg: n };
  return g;
}

const SHARED_GLSL = /* glsl */ `
  uniform float minLayer; uniform float maxLayer;
  uniform int mode;
  uniform vec3 toolColors[32]; uniform vec3 featureColors[32];
  uniform uint toolMask; uniform uint featureMask;
  uniform vec3 uniColor;
  bool visibleFor(vec4 inf) {
    int f = int(inf.y); int t = int(inf.z);
    return inf.x >= minLayer && inf.x <= maxLayer
      && ((featureMask >> uint(f)) & 1u) == 1u && ((toolMask >> uint(t)) & 1u) == 1u;
  }
  vec3 baseColor(vec4 inf) {
    if (mode == 2) return uniColor;
    return mode == 0 ? toolColors[int(inf.z)] : featureColors[int(inf.y)];
  }
`;

function makeUniforms() {
  const colorArr = (n) => Array.from({ length: n }, () => new THREE.Color(0x888888));
  return {
    minLayer: { value: 0 }, maxLayer: { value: 0 },
    mode: { value: 0 },
    toolColors: { value: colorArr(32) }, featureColors: { value: colorArr(32) },
    toolMask: { value: 0xffffffff >>> 0 }, featureMask: { value: 0xffffffff >>> 0 },
    halfWidth: { value: 0.21 },
    uniColor: { value: new THREE.Color(DEFAULT_UNI) },
  };
}

function makeLineMaterial(uniforms) {
  return new THREE.ShaderMaterial({
    uniforms,
    vertexShader: /* glsl */ `
      attribute vec4 info;
      ${SHARED_GLSL}
      varying vec3 vColor;
      void main() {
        if (!visibleFor(info)) {
          // Außerhalb des Sichtbereichs legen: die Linie wird verworfen, bevor sie gerastert wird
          gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
          vColor = vec3(0.0);
          return;
        }
        float shade = mod(info.w, ${POLY_END}.0) / 1000.0;
        if (info.x == maxLayer) shade = min(1.0, shade + 0.12);
        vColor = baseColor(info) * shade;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }`,
    fragmentShader: /* glsl */ `
      varying vec3 vColor;
      void main() { gl_FragColor = vec4(vColor, 1.0); }`,
  });
}

function makeTubeMaterial(uniforms) {
  return new THREE.ShaderMaterial({
    uniforms,
    side: THREE.DoubleSide, // die Bänder richten sich zur Kamera aus, die Wickelrichtung ist egal
    vertexShader: /* glsl */ `
      attribute vec3 aStart; attribute vec3 aEnd; attribute vec4 aInfo;
      uniform float halfWidth;
      ${SHARED_GLSL}
      varying vec3 vColor; varying float vSide; varying vec3 vPerp; varying vec3 vToCam;
      void main() {
        vec4 s = modelViewMatrix * vec4(aStart, 1.0);
        vec4 e = modelViewMatrix * vec4(aEnd, 1.0);
        vec3 d = e.xyz - s.xyz;
        float len = length(d);
        if (aInfo.w >= ${POLY_END}.0 || len < 1e-5 || !visibleFor(aInfo)) {
          gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
          return;
        }
        d /= len;
        // Band quer zur Blickrichtung aufspannen, Enden um die halbe Breite verlängern
        vec2 corner = position.xy;
        vec3 mid = mix(s.xyz, e.xyz, corner.x);
        vec3 toCam = normalize(-mid);
        vec3 perp = cross(d, toCam);
        float pl = length(perp);
        perp = pl > 1e-4 ? perp / pl : vec3(1.0, 0.0, 0.0);
        vec3 p = mid + perp * halfWidth * corner.y + d * halfWidth * (corner.x * 2.0 - 1.0);
        vSide = corner.y; vPerp = perp; vToCam = toCam;
        vColor = baseColor(aInfo);
        if (aInfo.x == maxLayer) vColor = min(vec3(1.0), vColor * 1.12);
        gl_Position = projectionMatrix * vec4(p, 1.0);
      }`,
    fragmentShader: /* glsl */ `
      varying vec3 vColor; varying float vSide; varying vec3 vPerp; varying vec3 vToCam;
      void main() {
        // Querschnitt einer Röhre: Normale aus der Position quer über das Band
        float c = sqrt(max(0.0, 1.0 - vSide * vSide));
        vec3 n = normalize(vPerp * vSide + vToCam * c);
        vec3 L = normalize(vec3(0.35, 0.55, 0.75));
        float diff = max(dot(n, L), 0.0);
        float spec = pow(max(dot(reflect(-L, n), vToCam), 0.0), 20.0) * 0.28;
        // Dunkle Filamente an beleuchteten Stellen etwas aufhellen, sonst verschwinden die Schichten
        float luma = dot(vColor, vec3(0.299, 0.587, 0.114));
        vec3 col = vColor * (0.38 + 0.62 * diff) + vec3(0.22 * (1.0 - luma) * diff * diff) + spec;
        gl_FragColor = vec4(col, 1.0);
      }`,
  });
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#222";
}

function start({ meta, pts, polys }) {
  const canvas = document.getElementById("viewer-canvas");
  // Hohe Pixeldichte ist selbst schon glatt, Kantenglättung nur für normale Displays
  const dpr = window.devicePixelRatio || 1;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: dpr < 1.5, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(dpr, 2));
  const scene = new THREE.Scene();
  const setBg = () => { scene.background = new THREE.Color(cssVar("--viewer-bg")); };
  setBg();
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { setBg(); render(); });

  const [minx, miny, maxx, maxy, maxz] = meta.bbox;
  const cx = (minx + maxx) / 2, cy = (miny + maxy) / 2, cz = maxz / 2;
  const size = Math.max(maxx - minx, maxy - miny, maxz, 10);

  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 5000);
  camera.up.set(0, 0, 1);
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.12;

  const resetView = () => {
    controls.target.set(cx, cy, cz);
    camera.position.set(cx + size * 1.1, cy - size * 1.6, cz + size * 1.1);
    controls.update();
  };
  const topView = () => {
    controls.target.set(cx, cy, 0);
    camera.position.set(cx, cy - 0.001, size * 2.2);
    controls.update();
  };

  // Druckplatte als Raster, ausgerichtet am Druckbereich
  const bed = Math.ceil(Math.max(256, maxx + 10, maxy + 10) / 10) * 10;
  const grid = new THREE.GridHelper(bed, bed / 10, cssVar("--grid-strong"), cssVar("--grid"));
  grid.rotation.x = Math.PI / 2;
  grid.position.set(bed / 2, bed / 2, -0.01);
  scene.add(grid);

  const { geo, tube } = buildGeometry(meta, pts, polys);
  const uniforms = makeUniforms();
  const mat = { uniforms }; // gemeinsame Einstellungen für Linien und Röhren
  uniforms.halfWidth.value = (Number(root.dataset.nozzle) || 0.4) * 1.05 / 2;
  const lineMat = makeLineMaterial(uniforms);
  const tubeMat = makeTubeMaterial(uniforms);

  const lines = new THREE.LineSegments(geo, lineMat);
  const tubes = new THREE.Mesh(tube, tubeMat);
  for (const o of [lines, tubes]) { o.frustumCulled = false; o.visible = false; scene.add(o); }

  // Darstellungen, von schön nach schnell. Ausgedünnte Linien entstehen bei Bedarf.
  const lods = new Map(); // Schrittweite -> LineSegments
  const lodFor = (step) => {
    if (!lods.has(step)) {
      const o = new THREE.LineSegments(buildLod(meta, polys, geo, step), lineMat);
      o.frustumCulled = false; o.visible = false; scene.add(o);
      lods.set(step, o);
    }
    return lods.get(step);
  };
  let range = { lo: 0, hi: meta.layers.length - 1 };
  const applyRange = (obj) => {
    const { lo, hi } = range;
    if (obj === tubes) {
      const lp = tube.userData.layerPoint;
      tube.instanceCount = Math.max(0, Math.min(lp[hi + 1], tube.userData.nPts - 1));
      tube.setDrawRange(0, 6);
    } else {
      const ls = obj.geometry.userData.layerStart;
      obj.geometry.setDrawRange(ls[lo], ls[hi + 1] - ls[lo]);
    }
  };
  const show = (obj) => {
    for (const o of [lines, tubes, ...lods.values()]) o.visible = o === obj;
    applyRange(obj);
  };
  // Anteil der sichtbaren Bahnen, um die Messwerte auf den Schichtbereich umzurechnen
  const visibleShare = () => {
    const ls = geo.userData.layerStart;
    return Math.max(0.001, (ls[range.hi + 1] - ls[range.lo]) / Math.max(1, ls[ls.length - 1]));
  };

  // Farben: Werkzeug t = Filament-Slot t + 1. Ohne Werkzeugwechsel gilt das einzige Filament.
  const bySlot = Object.fromEntries(filaments.map((f) => [f.slot, f]));
  const toolFil = (t) => bySlot[t + 1] || (meta.tools.length === 0 ? filaments[0] : null);
  for (let t = 0; t < 32; t++) {
    const f = toolFil(t);
    if (f) mat.uniforms.toolColors.value[t].set(f.color);
  }
  meta.features.forEach((name, i) => mat.uniforms.featureColors.value[i].set(FEATURE_COLORS[name] || "#7F868C"));

  // Schichtregler
  const nLayers = meta.layers.length;
  const slider = document.getElementById("layer-max");
  const single = document.getElementById("layer-single");
  const label = document.getElementById("layer-label");
  slider.max = nLayers - 1;
  slider.value = nLayers - 1;
  const fmt = (v) => v.toLocaleString("de-DE", { maximumFractionDigits: 2 });
  const applyLayers = () => {
    const v = Number(slider.value);
    const lo = single.checked ? v : 0;
    mat.uniforms.maxLayer.value = v;
    mat.uniforms.minLayer.value = lo;
    range = { lo, hi: v };
    // Nur den sichtbaren Schichtbereich an die Grafikkarte schicken
    show(restObject());
    label.textContent = `${v + 1} von ${nLayers} (${fmt(meta.layers[v] || 0)} mm)`;
    render();
  };
  slider.addEventListener("input", applyLayers);
  single.addEventListener("change", applyLayers);
  window.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" && e.target.type !== "range") return;
    if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      e.preventDefault();
      slider.value = Number(slider.value) + (e.key === "ArrowUp" ? 1 : -1);
      applyLayers();
    }
  });

  // Legende mit Ein-/Ausblenden
  const legend = document.getElementById("legend");
  const buildLegend = () => {
    const mode = mat.uniforms.mode.value;
    uniRow.hidden = mode !== MODES.uni;
    const items = mode !== MODES.feature
      ? (meta.tools.length ? meta.tools : [0]).map((t) => {
          const f = toolFil(t);
          return { bit: t, color: f ? f.color : "#888888", text: f ? `Slot ${f.slot}: ${f.type}` : `Werkzeug ${t}`, sub: f?.name || "" };
        })
      : meta.features
          .map((name, i) => ({ bit: i, color: FEATURE_COLORS[name] || "#7F868C", text: name, sub: "" }))
          .sort((a, b) => featureRank(a.text) - featureRank(b.text));
    const maskKey = mode !== MODES.feature ? "toolMask" : "featureMask";
    legend.replaceChildren(...items.map((it) => {
      const li = document.createElement("li");
      const lab = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = ((mat.uniforms[maskKey].value >>> it.bit) & 1) === 1;
      cb.addEventListener("change", () => {
        const m = mat.uniforms[maskKey].value;
        mat.uniforms[maskKey].value = (cb.checked ? m | (1 << it.bit) : m & ~(1 << it.bit)) >>> 0;
        render();
      });
      const sw = document.createElement("span");
      sw.className = "swatch";
      sw.style.background = it.color;
      const txt = document.createElement("span");
      txt.textContent = it.text;
      lab.append(cb, sw, txt);
      if (it.sub) {
        const s = document.createElement("span");
        s.className = "legend-sub";
        s.textContent = it.sub;
        lab.append(s);
      }
      li.append(lab);
      return li;
    }));
  };
  // Einfärbung: Filament, Linientyp oder einfarbig; Wahl und Farbe werden gemerkt
  // Fehlen Bedienelemente (z. B. alte Seitenvorlage), läuft die Vorschau trotzdem
  const uniRow = document.getElementById("uni-row") || { hidden: true };
  const uniInput = document.getElementById("uni-color");
  const uniValue = store.get("druckarchiv-uni-color", DEFAULT_UNI);
  mat.uniforms.uniColor.value.set(uniValue);
  if (uniInput) {
    uniInput.value = uniValue;
    uniInput.addEventListener("input", () => {
      mat.uniforms.uniColor.value.set(uniInput.value);
      store.set("druckarchiv-uni-color", uniInput.value);
      render();
    });
  }
  const setMode = (name) => {
    if (!document.querySelector(`[data-mode="${name}"]`)) name = "tool";
    mat.uniforms.mode.value = MODES[name] ?? 0;
    document.querySelectorAll("[data-mode]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === name)));
    store.set("druckarchiv-color-mode", name);
    buildLegend();
    render();
  };
  document.querySelectorAll("[data-mode]").forEach((btn) => btn.addEventListener("click", () => setMode(btn.dataset.mode)));
  document.getElementById("view-reset").addEventListener("click", () => { resetView(); render(); });
  document.getElementById("view-top").addEventListener("click", () => { topView(); render(); });

  // Rendern nur bei Bedarf, das schont Akku und Lüfter
  let pending = false;
  function render() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      if (controls.update()) render(); // Dämpfung ausklingen lassen
      renderer.render(scene, camera);
    });
  }
  // --- Automatische Abstimmung auf die Leistung des Geräts -------------------
  // Gemessene Bildzeiten bei voller Sichtbarkeit (ms). Aus ihnen wird für jeden
  // Schichtbereich die schönste Darstellung gewählt, die noch flüssig läuft.
  const perf = { tube: null, line: null };
  const restObject = () =>
    perf.tube === null || perf.tube * visibleShare() <= REST_MAX_MS ? tubes : lines;
  const motionObject = () => {
    const share = visibleShare();
    if (perf.tube !== null && perf.tube * share <= TARGET_MS) return tubes;
    if (perf.line === null || perf.line * share <= TARGET_MS) return lines;
    const step = Math.min(64, 2 ** Math.ceil(Math.log2((perf.line * share) / TARGET_MS)));
    return lodFor(step);
  };

  // Bildzeit messen: mehrere Bilder nacheinander, jeweils bis die Grafikkarte fertig ist.
  // Ein Pixel zurückzulesen zwingt den Browser, das Bild wirklich fertigzustellen.
  const gl = renderer.getContext();
  const px = new Uint8Array(4);
  const sync = () => gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
  const measure = (obj, frames) => {
    show(obj);
    renderer.render(scene, camera); sync(); // Aufwärmen, Puffer hochladen
    let total = 0, n = 0;
    for (; n < frames; n++) {
      const t0 = performance.now();
      renderer.render(scene, camera);
      sync();
      const dt = performance.now() - t0;
      total += dt;
      if (dt > 150) { n++; break; } // sehr langsam: eine Messung reicht
    }
    return total / n;
  };
  const calibrate = () => {
    const saved = range;
    range = { lo: 0, hi: meta.layers.length - 1 };
    perf.line = measure(lines, 4);
    // Röhren kosten ein Vielfaches der Linien; bei sehr langsamen Linien gar nicht erst messen
    perf.tube = perf.line > 150 ? perf.line * 8 : measure(tubes, 3);
    range = saved;
  };

  // Im Betrieb nachjustieren: ruckelt es trotzdem, gilt die Darstellung als langsamer
  const frameTimes = [];
  let lastFrame = 0;
  const trackFrame = (obj) => {
    const now = performance.now();
    if (lastFrame && now - lastFrame < 500) frameTimes.push(now - lastFrame);
    lastFrame = now;
    if (frameTimes.length < 12) return;
    const avg = frameTimes.reduce((x, y) => x + y, 0) / frameTimes.length;
    frameTimes.length = 0;
    if (avg > 45) {
      const share = visibleShare();
      if (obj === tubes) perf.tube = Math.max(perf.tube, (TARGET_MS + 1) / share);
      else if (obj === lines) perf.line = Math.max(perf.line, (TARGET_MS + 1) / share);
      else perf.line *= 2;
    }
  };

  // Beim Bewegen die flüssige Darstellung, kurz nach dem Stillstand die schönste
  let moving = false;
  let idleTimer = 0;
  controls.addEventListener("change", () => {
    const obj = motionObject();
    if (!moving || !obj.visible) show(obj);
    moving = true;
    trackFrame(obj);
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      moving = false;
      lastFrame = 0;
      show(restObject());
      render();
    }, IDLE_MS);
    render();
  });
  const resize = () => {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    render();
  };
  new ResizeObserver(resize).observe(canvas);

  resetView();
  setMode(store.get("druckarchiv-color-mode", "tool"));
  resize();
  loadingText.textContent = "Darstellung wird abgestimmt";
  const w = canvas.clientWidth, h = canvas.clientHeight;
  renderer.setSize(w, h, false);
  calibrate();
  applyLayers();
  loading.hidden = true;
  panel.hidden = false;
}

(async () => {
  try {
    await waitForPreview();
    start(await loadData());
  } catch (err) {
    loadingBar.hidden = true;
    loadingText.textContent = `Die Vorschau konnte nicht erstellt werden: ${err.message}`;
  }
})();
