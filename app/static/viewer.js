// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Schnuecks
import * as THREE from "three";
import { OrbitControls } from "/static/vendor/OrbitControls.js";
import { POLY_END, loadPreview } from "druckarchiv/geometry";

// Übersetzungen kommen aus der Seite (<script id="i18n">), Schlüssel ist der deutsche Text
const I18N = (() => {
  try { return JSON.parse(document.getElementById("i18n").textContent); } catch (e) { return {}; }
})();
const t = (key, vars = {}) => (I18N[key] || key).replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m));
const LOCALE = document.documentElement.lang || "de";

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
const MODES = { tool: 0, feature: 1, uni: 2, speed: 3, flow: 4, fan: 5, temp: 6 };
const METRIC_KEYS = ["speed", "flow", "fan", "temp"];
// Farbskala wie in den Slicern: langsam/kühl blau, schnell/heiß rot
const RAMP = ["#2A4DD9", "#1AB3D9", "#4DC759", "#F2D133", "#E63A2E"];
const EXTRA_COLORS = { travel: "#5B8DEF", retract: "#E040FB", seam: "#F5F5F5" };
const METRIC_NAMES = { speed: "Geschwindigkeit", flow: "Volumenstrom", fan: "Lüfter", temp: "Düsentemperatur" };
const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* privat */ } },
};
const IDLE_MS = 180;          // so lange nach der letzten Bewegung wieder volle Qualität
const TARGET_MS = 22;         // Bildzeit, die beim Drehen noch als flüssig gilt
const REST_MAX_MS = 400;      // länger darf ein einzelnes Standbild nicht dauern
const CUT_ALL = 1e9;          // Wiedergabe aus: oberste Schicht vollständig
const PLAY_MM_S = 150;        // Wiedergabe bei 1×: etwa übliche Druckgeschwindigkeit
const PLAY_SPEEDS = [1, 10, 50];

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
    if (!res.ok) throw new Error(t("Vorschau nicht verfügbar"));
    const st = await res.json();
    if (st.state === "ready") return;
    if (st.state === "error") throw new Error(st.error || t("Aufbereitung fehlgeschlagen"));
    loadingText.textContent = t("G-Code wird aufbereitet: {pct} %", { pct: Math.round((st.progress || 0) * 100) });
    loadingBar.value = (st.progress || 0) * 100;
    await sleep(1000);
  }
}

// Laden und Aufbereiten im Web Worker; ohne Worker-Unterstützung direkt hier
function loadData() {
  loadingText.textContent = t("Druckbahnen werden geladen");
  loadingBar.removeAttribute("value");
  const progress = (phase, value) => {
    if (phase !== "build") return;
    loadingText.textContent = t("Geometrie wird aufgebaut: {pct} %", { pct: Math.round(value * 100) });
    loadingBar.value = value * 100;
  };
  const inline = () => loadPreview(api, progress);
  if (typeof Worker === "undefined") return inline();
  return new Promise((resolve, reject) => {
    let worker;
    try {
      worker = new Worker(`/static/geometry-worker.js?v=${root.dataset.assets || ""}`, { type: "module" });
    } catch (e) {
      resolve(inline());
      return;
    }
    worker.onmessage = (e) => {
      const msg = e.data;
      if (msg.type === "progress") progress(msg.phase, msg.value);
      else if (msg.type === "done") { worker.terminate(); resolve(msg.data); }
      else if (msg.type === "error") { worker.terminate(); reject(new Error(msg.message)); }
    };
    // Worker nicht startbar (z. B. alte Browser ohne Modul-Worker): im Hauptthread weiter
    worker.onerror = (e) => { e.preventDefault(); worker.terminate(); resolve(inline()); };
    worker.postMessage({ api });
  });
}

function buildGeometry(d) {
  const { pos, info, mets, index, layerStart, layerPoint, nSeg } = d;
  const nPts = pos.length / 3;
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("info", new THREE.BufferAttribute(info, 4));
  geo.setAttribute("metric", new THREE.BufferAttribute(mets, 4));
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
  // Messwerte gehören zum Punkt am Ende des Bahnstücks
  const metBuf = new THREE.InstancedInterleavedBuffer(mets, 4, 1);
  tube.setAttribute("aMetric", new THREE.InterleavedBufferAttribute(metBuf, 4, 4));
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
  g.setAttribute("metric", geo.getAttribute("metric"));
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
  uniform float cutPoint; // Wiedergabe: oberste Schicht nur bis zu diesem Punkt (mit Bruchteil)
  bool visibleFor(vec4 inf) {
    int f = int(inf.y); int t = int(inf.z);
    return inf.x >= minLayer && inf.x <= maxLayer
      && ((featureMask >> uint(f)) & 1u) == 1u && ((toolMask >> uint(t)) & 1u) == 1u;
  }
  uniform vec4 metricLo; uniform vec4 metricHi;
  uniform vec3 ramp[5];
  vec3 rampColor(float t) {
    float x = clamp(t, 0.0, 1.0) * 4.0;
    int i = int(min(floor(x), 3.0));
    return mix(ramp[i], ramp[i + 1], x - float(i));
  }
  vec3 baseColor(vec4 inf, vec4 met) {
    if (mode >= 3) {
      int k = mode - 3;
      float span = max(metricHi[k] - metricLo[k], 1.0);
      return rampColor((met[k] - metricLo[k]) / span);
    }
    if (mode == 2) return uniColor;
    return mode == 0 ? toolColors[int(inf.z)] : featureColors[int(inf.y)];
  }
`;

function makeUniforms() {
  const colorArr = (n) => Array.from({ length: n }, () => new THREE.Color(0x888888));
  return {
    minLayer: { value: 0 }, maxLayer: { value: 0 },
    cutPoint: { value: CUT_ALL },
    mode: { value: 0 },
    toolColors: { value: colorArr(32) }, featureColors: { value: colorArr(32) },
    toolMask: { value: 0xffffffff >>> 0 }, featureMask: { value: 0xffffffff >>> 0 },
    halfWidth: { value: 0.21 },
    normalPass: { value: 0 },
    uniColor: { value: new THREE.Color(DEFAULT_UNI) },
    metricLo: { value: new THREE.Vector4() }, metricHi: { value: new THREE.Vector4(1, 1, 1, 1) },
    // Wie alle Farben linear gerechnet und bei der Ausgabe umgerechnet: entspricht der Legende
    ramp: { value: RAMP.map((h) => new THREE.Color(h)) },
  };
}

function makeLineMaterial(uniforms) {
  return new THREE.ShaderMaterial({
    uniforms,
    vertexShader: /* glsl */ `
      attribute vec4 info; attribute vec4 metric;
      ${SHARED_GLSL}
      varying vec3 vColor; varying float vIdx; varying float vTop;
      void main() {
        vIdx = float(gl_VertexID); // beim indexierten Zeichnen der Punktindex
        vTop = info.x == maxLayer ? 1.0 : 0.0;
        if (!visibleFor(info)) {
          // Außerhalb des Sichtbereichs legen: die Linie wird verworfen, bevor sie gerastert wird
          gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
          vColor = vec3(0.0);
          return;
        }
        float shade = mod(info.w, ${POLY_END}.0) / 1000.0;
        if (info.x == maxLayer) shade = min(1.0, shade + 0.12);
        vColor = baseColor(info, metric) * shade;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }`,
    fragmentShader: /* glsl */ `
      uniform float cutPoint;
      varying vec3 vColor; varying float vIdx; varying float vTop;
      void main() {
        if (vTop > 0.5 && vIdx > cutPoint) discard;
        gl_FragColor = vec4(vColor, 1.0);
        #include <colorspace_fragment>
      }`,
  });
}

function makeTubeMaterial(uniforms) {
  return new THREE.ShaderMaterial({
    uniforms,
    side: THREE.DoubleSide, // die Bänder richten sich zur Kamera aus, die Wickelrichtung ist egal
    vertexShader: /* glsl */ `
      attribute vec3 aStart; attribute vec3 aEnd; attribute vec4 aInfo; attribute vec4 aMetric;
      uniform float halfWidth;
      ${SHARED_GLSL}
      varying vec3 vColor; varying float vSide; varying vec3 vPerp; varying vec3 vToCam;
      void main() {
        vec4 s = modelViewMatrix * vec4(aStart, 1.0);
        vec4 e = modelViewMatrix * vec4(aEnd, 1.0);
        vec3 d = e.xyz - s.xyz;
        float len = length(d);
        float cut = aInfo.x == maxLayer ? cutPoint - float(gl_InstanceID) : 1.0;
        if (aInfo.w >= ${POLY_END}.0 || len < 1e-5 || !visibleFor(aInfo) || cut <= 0.0) {
          gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
          return;
        }
        if (cut < 1.0) { e.xyz = mix(s.xyz, e.xyz, cut); len *= cut; }
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
        vColor = baseColor(aInfo, aMetric);
        if (aInfo.x == maxLayer) vColor = min(vec3(1.0), vColor * 1.12);
        gl_Position = projectionMatrix * vec4(p, 1.0);
      }`,
    fragmentShader: /* glsl */ `
      uniform int normalPass;
      varying vec3 vColor; varying float vSide; varying vec3 vPerp; varying vec3 vToCam;
      void main() {
        // Querschnitt einer Röhre: Normale aus der Position quer über das Band
        float c = sqrt(max(0.0, 1.0 - vSide * vSide));
        vec3 n = normalize(vPerp * vSide + vToCam * c);
        if (normalPass == 1) { gl_FragColor = vec4(n * 0.5 + 0.5, 1.0); return; }
        vec3 L = normalize(vec3(0.35, 0.55, 0.75));
        float diff = max(dot(n, L), 0.0);
        float spec = pow(max(dot(reflect(-L, n), vToCam), 0.0), 20.0) * 0.28;
        // Dunkle Filamente an beleuchteten Stellen etwas aufhellen, sonst verschwinden die Schichten
        float luma = dot(vColor, vec3(0.2126, 0.7152, 0.0722));
        vec3 col = vColor * (0.22 + 0.78 * diff) + vec3(0.06 * (1.0 - luma) * diff * diff) + spec * 0.7;
        gl_FragColor = vec4(col, 1.0);
        #include <colorspace_fragment>
      }`,
  });
}

// Umgebungsverdeckung aus dem Tiefenpuffer: Rillen zwischen den Schichten und
// Ecken werden abgedunkelt. Wird als Faktor multiplikativ über das Bild gelegt.
// Leerfahrten: dünne Linien, sichtbar im gewählten Schichtbereich
function makeTravelMaterial(uniforms, color) {
  return new THREE.ShaderMaterial({
    uniforms: { ...uniforms, color: { value: new THREE.Color(color) } },
    transparent: true, depthWrite: false,
    vertexShader: /* glsl */ `
      attribute float layer;
      uniform float minLayer; uniform float maxLayer;
      void main() {
        if (layer < minLayer || layer > maxLayer) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); return; }
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }`,
    fragmentShader: /* glsl */ `
      uniform vec3 color;
      void main() {
        gl_FragColor = vec4(color, 0.75);
        #include <colorspace_fragment>
      }`,
  });
}

// Rückzüge und Nahtstellen: runde Punkte mit dunklem Rand, leicht zur Kamera versetzt,
// damit sie nicht in der Röhre verschwinden, auf der sie liegen
function makeMarkerMaterial(uniforms, color, size) {
  return new THREE.ShaderMaterial({
    uniforms: { ...uniforms, color: { value: new THREE.Color(color) }, size: { value: size }, lift: { value: 0.5 } },
    vertexShader: /* glsl */ `
      attribute float layer;
      uniform float minLayer; uniform float maxLayer; uniform float size; uniform float lift;
      void main() {
        if (layer < minLayer || layer > maxLayer) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); gl_PointSize = 0.0; return; }
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        mv.xyz += normalize(-mv.xyz) * lift;
        gl_Position = projectionMatrix * mv;
        gl_PointSize = size;
      }`,
    fragmentShader: /* glsl */ `
      uniform vec3 color;
      void main() {
        float r = length(gl_PointCoord - 0.5) * 2.0;
        if (r > 1.0) discard;
        gl_FragColor = vec4(r > 0.7 ? vec3(0.01) : color, 1.0);
        #include <colorspace_fragment>
      }`,
  });
}

const FULLSCREEN_VERT = /* glsl */ `
  varying vec2 vUv;
  void main() { vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }`;
const VIEWPOS_GLSL = /* glsl */ `
  uniform sampler2D tDepth; uniform mat4 projInv;
  vec3 viewPos(vec2 uv) {
    vec4 v = projInv * vec4(uv * 2.0 - 1.0, texture2D(tDepth, uv).x * 2.0 - 1.0, 1.0);
    return v.xyz / v.w;
  }`;

function makeAoMaterial(depthTexture, normalTexture) {
  return new THREE.ShaderMaterial({
    uniforms: {
      tDepth: { value: depthTexture }, tNormal: { value: normalTexture },
      proj: { value: new THREE.Matrix4() }, projInv: { value: new THREE.Matrix4() },
      radius: { value: 1.2 }, strength: { value: 1.0 }, pxScale: { value: 500 },
    },
    depthTest: false, depthWrite: false, toneMapped: false, blending: THREE.NoBlending,
    vertexShader: FULLSCREEN_VERT,
    fragmentShader: /* glsl */ `
      ${VIEWPOS_GLSL}
      uniform sampler2D tNormal; uniform mat4 proj;
      uniform float radius; uniform float strength; uniform float pxScale;
      varying vec2 vUv;
      void main() {
        if (texture2D(tDepth, vUv).x >= 0.99999) { gl_FragColor = vec4(1.0); return; }
        vec3 p = viewPos(vUv);
        // Suchradius in Pixeln: zu klein gibt nur Moiré auf den feinen Rillen, dann keine Schattierung
        float fade = smoothstep(10.0, 24.0, radius * pxScale / max(-p.z, 1e-3));
        if (fade <= 0.0) { gl_FragColor = vec4(1.0); return; }
        vec3 n = normalize(texture2D(tNormal, vUv).xyz * 2.0 - 1.0);
        vec3 tg = normalize(cross(n, abs(n.z) < 0.9 ? vec3(0.0, 0.0, 1.0) : vec3(1.0, 0.0, 0.0)));
        vec3 bt = cross(n, tg);
        // Pro Pixel gedrehtes Muster (Interleaved Gradient Noise); das Rauschen glättet der Weichzeichner
        float ang = 6.2831853 * fract(52.9829189 * fract(dot(gl_FragCoord.xy, vec2(0.06711056, 0.00583715))));
        float bias = 0.002 * abs(p.z);
        float occ = 0.0;
        for (int i = 0; i < 16; i++) {
          float fi = float(i);
          float r = (fi + 0.5) / 16.0;
          float a = ang + fi * 2.39996; // goldener Winkel
          vec3 sp = p + radius * r * (tg * cos(a) + bt * sin(a)) + n * radius * (0.15 + 0.5 * sqrt(1.0 - r * r)) * r;
          vec4 c = proj * vec4(sp, 1.0);
          vec2 suv = c.xy / c.w * 0.5 + 0.5;
          if (suv.x < 0.0 || suv.y < 0.0 || suv.x > 1.0 || suv.y > 1.0) continue;
          float sz = viewPos(suv).z;
          float range = smoothstep(0.0, 1.0, radius / max(abs(p.z - sz), 1e-4));
          occ += (sz >= sp.z + bias ? 1.0 : 0.0) * range;
        }
        float ao = mix(1.0, clamp(1.0 - strength * occ / 16.0, 0.4, 1.0), fade);
        gl_FragColor = vec4(vec3(ao), 1.0);
      }`,
  });
}

// Weichzeichner mit Rücksicht auf Kanten (nur Nachbarn in ähnlicher Tiefe), legt die
// Schattierung multiplikativ über das fertige, kantengeglättete Bild
function makeAoBlurMaterial(depthTexture, aoTexture) {
  return new THREE.ShaderMaterial({
    uniforms: {
      tDepth: { value: depthTexture }, tAo: { value: aoTexture },
      projInv: { value: new THREE.Matrix4() }, texel: { value: new THREE.Vector2() },
    },
    depthTest: false, depthWrite: false, toneMapped: false,
    blending: THREE.CustomBlending, blendEquation: THREE.AddEquation,
    blendSrc: THREE.DstColorFactor, blendDst: THREE.ZeroFactor,
    vertexShader: FULLSCREEN_VERT,
    fragmentShader: /* glsl */ `
      ${VIEWPOS_GLSL}
      uniform sampler2D tAo; uniform vec2 texel;
      varying vec2 vUv;
      void main() {
        if (texture2D(tDepth, vUv).x >= 0.99999) { gl_FragColor = vec4(1.0); return; }
        float z0 = viewPos(vUv).z;
        float sum = 0.0, wsum = 0.0;
        for (int x = -2; x <= 2; x++) {
          for (int y = -2; y <= 2; y++) {
            vec2 uv = vUv + vec2(float(x), float(y)) * texel;
            float w = 1.0 - smoothstep(0.0, 0.01 * abs(z0), abs(viewPos(uv).z - z0));
            sum += texture2D(tAo, uv).r * w;
            wsum += w;
          }
        }
        gl_FragColor = vec4(vec3(sum / max(wsum, 1e-4)), 1.0);
      }`,
  });
}

// Textur der Druckplatte: feine Struktur, Linien alle 10 mm, kräftiger alle 50 mm, Rand
function makePlateCanvas(bed) {
  const px = 8; // Pixel pro mm
  const size = Math.min(4096, bed * px);
  const s = size / bed;
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const g = c.getContext("2d");
  const base = new THREE.Color(cssVar("--bed"));
  const hex = (col, f) => `#${col.clone().offsetHSL(0, 0, f).getHexString()}`;
  g.fillStyle = hex(base, 0.02);
  g.fillRect(0, 0, size, size);
  // Struktur wie bei einer texturierten PEI-Platte: viele kleine, leicht hellere Punkte
  let seed = 7;
  const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
  g.fillStyle = hex(base, 0.06);
  for (let i = 0; i < size * size / 90; i++) g.fillRect(rnd() * size, rnd() * size, 1.4, 1.4);
  const line = (mm, width, color) => {
    g.strokeStyle = color;
    g.lineWidth = width;
    g.beginPath();
    for (let v = mm; v < bed; v += mm) {
      g.moveTo(v * s, 0); g.lineTo(v * s, size);
      g.moveTo(0, v * s); g.lineTo(size, v * s);
    }
    g.stroke();
  };
  line(10, 1.2, hex(base, 0.07));
  line(50, 2.2, hex(base, 0.13));
  g.strokeStyle = hex(base, 0.2);
  g.lineWidth = 3 * s;
  g.strokeRect(1.5 * s, 1.5 * s, size - 3 * s, size - 3 * s);
  return c;
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#222";
}

function start(data) {
  const { meta, polys, travels, retracts } = data;
  const canvas = document.getElementById("viewer-canvas");
  // Hohe Pixeldichte ist selbst schon glatt, Kantenglättung nur für normale Displays
  const dpr = window.devicePixelRatio || 1;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: dpr < 1.5, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(dpr, 2));
  const scene = new THREE.Scene();
  const setBg = () => { scene.background = new THREE.Color(cssVar("--viewer-bg")); };
  setBg();
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { setBg(); refreshPlate(); render(); });

  const [minx, miny, maxx, maxy, maxz] = meta.bbox;
  const cx = (minx + maxx) / 2, cy = (miny + maxy) / 2, cz = maxz / 2;
  const size = Math.max(maxx - minx, maxy - miny, maxz, 10);

  const camera = new THREE.PerspectiveCamera(40, 1, 0.5, 5000);
  camera.up.set(0, 0, 1);
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.12;
  controls.zoomToCursor = true;

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

  // Druckplatte mit strukturierter Oberfläche, 10-mm- und 50-mm-Linien und Rand.
  // Sie wird in der Tiefenschattierung mitgerechnet: das Modell wirft einen weichen
  // Kontaktschatten auf die Platte.
  const bed = Math.ceil(Math.max(256, maxx + 10, maxy + 10) / 10) * 10;
  const plateTex = new THREE.CanvasTexture(makePlateCanvas(bed));
  plateTex.colorSpace = THREE.SRGBColorSpace;
  plateTex.anisotropy = renderer.capabilities.getMaxAnisotropy();
  const bedGroup = new THREE.Group();
  const plateMesh = new THREE.Mesh(new THREE.PlaneGeometry(bed, bed), new THREE.MeshBasicMaterial({ map: plateTex }));
  plateMesh.position.set(bed / 2, bed / 2, -0.02);
  const rim = new THREE.Mesh(new THREE.BoxGeometry(bed + 6, bed + 6, 3), new THREE.MeshBasicMaterial({ color: cssVar("--bed") }));
  rim.position.set(bed / 2, bed / 2, -1.55);
  bedGroup.add(rim, plateMesh);
  scene.add(bedGroup);
  const plateNormalMat = new THREE.MeshNormalMaterial();
  const plateBasicMat = plateMesh.material;
  const refreshPlate = () => {
    plateTex.image = makePlateCanvas(bed);
    plateTex.needsUpdate = true;
    rim.material.color.set(cssVar("--bed"));
  };

  const { geo, tube, pos } = buildGeometry(data);
  const uniforms = makeUniforms();
  const mat = { uniforms }; // gemeinsame Einstellungen für Linien und Röhren
  uniforms.halfWidth.value = (Number(root.dataset.nozzle) || 0.4) * 1.05 / 2;
  const lineMat = makeLineMaterial(uniforms);
  const tubeMat = makeTubeMaterial(uniforms);

  const lines = new THREE.LineSegments(geo, lineMat);
  const tubes = new THREE.Mesh(tube, tubeMat);
  for (const o of [lines, tubes]) { o.frustumCulled = false; o.visible = false; scene.add(o); }

  // --- Leerfahrten, Rückzüge und Nahtstellen -------------------------------------------
  const layerZ = (l) => meta.layers[l] || 0;
  const unq = (v) => v / meta.scale - meta.offset;
  const extraLayerGeo = (positions, layers) => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    g.setAttribute("layer", new THREE.BufferAttribute(layers, 1));
    g.boundingSphere = geo.boundingSphere;
    return g;
  };
  const nTr = travels.length / 5;
  const trPos = new Float32Array(nTr * 6), trLayer = new Float32Array(nTr * 2);
  for (let i = 0; i < nTr; i++) {
    const l = travels[i * 5 + 4], z = layerZ(l) + uniforms.halfWidth.value;
    trPos.set([unq(travels[i * 5]), unq(travels[i * 5 + 1]), z, unq(travels[i * 5 + 2]), unq(travels[i * 5 + 3]), z], i * 6);
    trLayer[i * 2] = trLayer[i * 2 + 1] = l;
  }
  const nRe = retracts.length / 3;
  const rePos = new Float32Array(nRe * 3), reLayer = new Float32Array(nRe);
  for (let i = 0; i < nRe; i++) {
    const l = retracts[i * 3 + 2];
    rePos.set([unq(retracts[i * 3]), unq(retracts[i * 3 + 1]), layerZ(l)], i * 3);
    reLayer[i] = l;
  }
  // Nahtstellen: Anfang jeder Außenwand-Bahn
  const outer = meta.features.indexOf("Außenwand");
  const seamIdx = [];
  if (outer >= 0) for (let i = 0; i < polys.length; i += 5) if (polys[i + 4] === outer) seamIdx.push(i);
  const sePos = new Float32Array(seamIdx.length * 3), seLayer = new Float32Array(seamIdx.length);
  seamIdx.forEach((i, k) => {
    const p = polys[i];
    sePos.set([pos[p * 3], pos[p * 3 + 1], pos[p * 3 + 2]], k * 3);
    seLayer[k] = polys[i + 2];
  });
  const pointScale = Math.min(dpr, 2);
  const extras = {
    travel: new THREE.LineSegments(extraLayerGeo(trPos, trLayer), makeTravelMaterial(uniforms, EXTRA_COLORS.travel)),
    retract: new THREE.Points(extraLayerGeo(rePos, reLayer), makeMarkerMaterial(uniforms, EXTRA_COLORS.retract, 7 * pointScale)),
    seam: new THREE.Points(extraLayerGeo(sePos, seLayer), makeMarkerMaterial(uniforms, EXTRA_COLORS.seam, 7 * pointScale)),
  };
  const extraCounts = { travel: nTr, retract: nRe, seam: seamIdx.length };
  for (const [key, obj] of Object.entries(extras)) {
    obj.frustumCulled = false;
    obj.renderOrder = 2;
    if (obj.material.uniforms.lift) obj.material.uniforms.lift.value = uniforms.halfWidth.value * 2.5;
    obj.visible = false;
    scene.add(obj);
    const box = document.getElementById(`show-${key}`);
    if (!box) continue;
    box.closest("label").querySelector(".extra-count").textContent = extraCounts[key].toLocaleString(LOCALE);
    box.disabled = extraCounts[key] === 0;
    box.checked = !box.disabled && store.get(`druckarchiv-show-${key}`, "0") === "1";
    obj.visible = box.checked;
    box.addEventListener("change", () => {
      obj.visible = box.checked;
      store.set(`druckarchiv-show-${key}`, box.checked ? "1" : "0");
      render();
    });
  }

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
  const ranges = meta.metrics || {};
  METRIC_KEYS.forEach((key, i) => {
    const r = ranges[key] || { min: 0, max: 0, scale: 1 };
    uniforms.metricLo.value.setComponent(i, r.min * r.scale);
    uniforms.metricHi.value.setComponent(i, r.max * r.scale);
  });
  // Messwerte, die im G-Code fehlen (z. B. keine Lüfterbefehle), nicht zur Wahl stellen
  document.querySelectorAll("[data-mode]").forEach((b) => {
    const r = ranges[b.dataset.mode];
    if (METRIC_KEYS.includes(b.dataset.mode) && (!r || r.max <= 0)) b.disabled = true;
  });

  // Schichtregler
  const nLayers = meta.layers.length;
  const slider = document.getElementById("layer-max");
  const single = document.getElementById("layer-single");
  const label = document.getElementById("layer-label");
  slider.max = nLayers - 1;
  slider.value = nLayers - 1;
  const fmt = (v) => v.toLocaleString(LOCALE, { maximumFractionDigits: 2 });
  const applyLayers = (keepCut = false) => {
    const v = Number(slider.value);
    if (!keepCut) {
      playing = false;
      if (playBtn) { playBtn.textContent = "▶"; playBtn.setAttribute("aria-label", t("Abspielen")); playBtn.title = t("Abspielen"); }
      setCut(null);
    }
    const lo = single.checked ? v : 0;
    mat.uniforms.maxLayer.value = v;
    mat.uniforms.minLayer.value = lo;
    range = { lo, hi: v };
    // Nur den sichtbaren Schichtbereich an die Grafikkarte schicken
    show(playing ? motionObject() : restObject());
    label.textContent = t("{n} von {total} ({mm} mm)", { n: v + 1, total: nLayers, mm: fmt(meta.layers[v] || 0) });
    render();
  };
  slider.addEventListener("input", () => applyLayers());
  single.addEventListener("change", () => applyLayers());
  window.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" && e.target.type !== "range") return;
    if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      e.preventDefault();
      slider.value = Number(slider.value) + (e.key === "ArrowUp" ? 1 : -1);
      applyLayers();
    }
  });

  // --- Wiedergabe innerhalb der Schicht ------------------------------------------------
  // Bahnlänge bis zu jedem Punkt; Leerfahrten zählen nicht. Damit läuft die Wiedergabe
  // mit gleichmäßiger Geschwindigkeit in mm/s statt Punkt für Punkt.
  const nPts = pos.length / 3;
  const cum = data.cum; // Bahnlänge bis zu jedem Punkt, Leerfahrten zählen nicht
  const lp = tube.userData.layerPoint;
  const layerLen = (l) => {
    const a = lp[l], b = lp[l + 1] - 1;
    return b >= a ? [cum[a], cum[b]] : [0, 0];
  };
  // Strecke -> Punktindex mit Bruchteil (Instanz i verbindet Punkt i und i+1)
  const cutFor = (l, dist) => {
    let lo = lp[l], hi = Math.max(lp[l], lp[l + 1] - 1);
    if (dist >= cum[hi]) return hi;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (cum[mid] <= dist) lo = mid; else hi = mid; }
    const d = cum[hi] - cum[lo];
    return lo + (d > 0 ? (dist - cum[lo]) / d : 0);
  };

  // Düse: kleiner Kegel mit Spitze nach unten, zeigt die aktuelle Position
  const hw = uniforms.halfWidth.value;
  const nozzle = new THREE.Mesh(
    new THREE.ConeGeometry(hw * 3, hw * 8, 20),
    new THREE.MeshBasicMaterial({ color: 0xe0a64a }),
  );
  nozzle.rotation.x = -Math.PI / 2;
  nozzle.visible = false;
  scene.add(nozzle);

  const progress = document.getElementById("layer-progress");
  const playBtn = document.getElementById("play");
  const speedBtn = document.getElementById("play-speed");
  let playDist = null; // null: Schicht vollständig
  let playing = false;
  let speedIdx = Math.max(0, PLAY_SPEEDS.indexOf(Number(store.get("druckarchiv-play-speed", 10))));
  const setCut = (dist) => {
    const l = Number(slider.value);
    const [a, b] = layerLen(l);
    if (dist === null || dist >= b) {
      playDist = null;
      uniforms.cutPoint.value = CUT_ALL;
      nozzle.visible = false;
      if (progress) progress.value = progress.max;
      return;
    }
    playDist = Math.max(a, dist);
    const c = cutFor(l, playDist);
    uniforms.cutPoint.value = c;
    const i = Math.floor(c), f = c - i, j = Math.min(i + 1, nPts - 1);
    nozzle.position.set(
      pos[i * 3] + (pos[j * 3] - pos[i * 3]) * f,
      pos[i * 3 + 1] + (pos[j * 3 + 1] - pos[i * 3 + 1]) * f,
      pos[i * 3 + 2] + hw * 5,
    );
    nozzle.visible = true;
    if (progress) progress.value = b > a ? Math.round(((playDist - a) / (b - a)) * Number(progress.max)) : progress.max;
  };
  const setPlaying = (on) => {
    playing = on;
    if (playBtn) {
      playBtn.textContent = on ? "❚❚" : "▶";
      playBtn.setAttribute("aria-label", on ? t("Anhalten") : t("Abspielen"));
      playBtn.title = playBtn.getAttribute("aria-label");
    }
    if (on) {
      const l = Number(slider.value);
      if (playDist === null) {
        // Vom Ende aus neu starten: bei der untersten sichtbaren Schicht beginnen
        if (l >= nLayers - 1 || single.checked) { slider.value = single.checked ? l : 0; applyLayers(true); }
        setCut(layerLen(Number(slider.value))[0]);
      }
      lastTick = 0;
      requestAnimationFrame(tick);
    }
    render();
  };
  let lastTick = 0;
  const tick = (now) => {
    if (!playing) return;
    const dt = lastTick ? Math.min(0.1, (now - lastTick) / 1000) : 0;
    lastTick = now;
    let dist = (playDist ?? layerLen(Number(slider.value))[0]) + PLAY_MM_S * PLAY_SPEEDS[speedIdx] * dt;
    let l = Number(slider.value);
    while (dist >= layerLen(l)[1]) {
      if (single.checked || l >= nLayers - 1) { setCut(null); setPlaying(false); return; }
      dist = layerLen(l + 1)[0] + (dist - layerLen(l)[1]);
      slider.value = ++l;
      applyLayers(true);
    }
    setCut(dist);
    render();
    requestAnimationFrame(tick);
  };
  if (progress) {
    progress.addEventListener("input", () => {
      const [a, b] = layerLen(Number(slider.value));
      setCut(a + (b - a) * (Number(progress.value) / Number(progress.max)));
      render();
    });
  }
  playBtn?.addEventListener("click", () => setPlaying(!playing));
  const showSpeed = () => { if (speedBtn) speedBtn.textContent = `${PLAY_SPEEDS[speedIdx]}×`; };
  speedBtn?.addEventListener("click", () => {
    speedIdx = (speedIdx + 1) % PLAY_SPEEDS.length;
    store.set("druckarchiv-play-speed", PLAY_SPEEDS[speedIdx]);
    showSpeed();
  });
  showSpeed();
  window.addEventListener("keydown", (e) => {
    if (e.key !== " " || /^(INPUT|BUTTON|SELECT|TEXTAREA|SUMMARY)$/.test(e.target.tagName)) return;
    e.preventDefault();
    setPlaying(!playing);
  });

  // Legende mit Ein-/Ausblenden
  const legend = document.getElementById("legend");
  const metricLegend = (key) => {
    const r = ranges[key];
    const num = (v) => v.toLocaleString(LOCALE, { maximumFractionDigits: key === "flow" ? 1 : 0 });
    const li = document.createElement("li");
    li.className = "metric-legend";
    const title = document.createElement("span");
    title.textContent = t(METRIC_NAMES[key]);
    const bar = document.createElement("span");
    bar.className = "metric-bar";
    bar.style.background = `linear-gradient(90deg, ${RAMP.join(", ")})`;
    const scale = document.createElement("span");
    scale.className = "metric-scale";
    const lo = document.createElement("span");
    const hi = document.createElement("span");
    lo.textContent = `${num(r.min)} ${r.unit}`;
    hi.textContent = r.max > r.min ? `${num(r.max)} ${r.unit}` : "";
    scale.append(lo, hi);
    li.append(title, bar, scale);
    return li;
  };
  const buildLegend = () => {
    const mode = mat.uniforms.mode.value;
    uniRow.hidden = mode !== MODES.uni;
    if (mode >= MODES.speed) {
      legend.replaceChildren(metricLegend(METRIC_KEYS[mode - MODES.speed]));
      return;
    }
    const items = mode !== MODES.feature
      ? (meta.tools.length ? meta.tools : [0]).map((tool) => {
          const f = toolFil(tool);
          return { bit: tool, color: f ? f.color : "#888888", text: f ? `Slot ${f.slot}: ${f.type}` : t("Werkzeug {n}", { n: tool }), sub: f?.name || "" };
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
      txt.textContent = mode === MODES.feature ? t(it.text) : it.text;
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
    const btn = document.querySelector(`[data-mode="${name}"]`);
    if (!btn || btn.disabled) name = "tool";
    mat.uniforms.mode.value = MODES[name] ?? 0;
    document.querySelectorAll("[data-mode]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === name)));
    store.set("druckarchiv-color-mode", name);
    buildLegend();
    render();
  };
  document.querySelectorAll("[data-mode]").forEach((btn) => btn.addEventListener("click", () => setMode(btn.dataset.mode)));
  const camKey = `druckarchiv-cam-${fid}-${plate}`;
  let camSaveTimer = 0;
  const saveCamera = () => {
    clearTimeout(camSaveTimer);
    camSaveTimer = setTimeout(() => store.set(camKey, JSON.stringify({
      p: camera.position.toArray().map((v) => +v.toFixed(3)),
      t: controls.target.toArray().map((v) => +v.toFixed(3)),
    })), 400);
  };
  const restoreCamera = () => {
    try {
      const c = JSON.parse(store.get(camKey, "null"));
      if (!c || c.p.length !== 3 || c.t.length !== 3 || !c.p.concat(c.t).every(Number.isFinite)) return false;
      camera.position.fromArray(c.p);
      controls.target.fromArray(c.t);
      controls.update();
      return true;
    } catch (e) { return false; }
  };
  const forgetCamera = () => { clearTimeout(camSaveTimer); try { localStorage.removeItem(camKey); } catch (e) { /* privat */ } };
  const resetAll = () => { resetView(); forgetCamera(); render(); };
  document.getElementById("view-reset").addEventListener("click", resetAll);

  // Doppelklick bzw. Doppeltipp setzt die Ansicht zurück
  canvas.addEventListener("dblclick", resetAll);
  // Tippen = kurz und fast ohne Bewegung; nach einer Zwei-Finger-Geste zählt nichts als Tippen
  let lastTap = 0, tapX = 0, tapY = 0, touches = 0, multi = false, downAt = 0, downX = 0, downY = 0;
  canvas.addEventListener("pointerdown", (e) => {
    if (e.pointerType !== "touch") return;
    touches++;
    if (touches > 1) multi = true;
    downAt = performance.now(); downX = e.clientX; downY = e.clientY;
  });
  const touchEnd = (e, cancelled) => {
    if (e.pointerType !== "touch") return;
    touches = Math.max(0, touches - 1);
    const wasMulti = multi;
    if (touches === 0) multi = false;
    const now = performance.now();
    const isTap = !cancelled && !wasMulti && now - downAt < 250 && Math.hypot(e.clientX - downX, e.clientY - downY) < 10;
    if (!isTap) { lastTap = 0; return; }
    if (lastTap && now - lastTap < 350 && Math.hypot(e.clientX - tapX, e.clientY - tapY) < 30) {
      lastTap = 0;
      resetAll();
    } else {
      lastTap = now; tapX = e.clientX; tapY = e.clientY;
    }
  };
  canvas.addEventListener("pointerup", (e) => touchEnd(e, false));
  canvas.addEventListener("pointercancel", (e) => touchEnd(e, true));

  // Vollbild für Vorschau und Bedienfeld, auch mit der Taste F
  const fullBtn = document.getElementById("view-full");
  const toggleFull = () => {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else root.requestFullscreen?.().catch(() => { /* vom Browser abgelehnt */ });
  };
  if (fullBtn) {
    if (!document.fullscreenEnabled) fullBtn.hidden = true;
    fullBtn.addEventListener("click", toggleFull);
    document.addEventListener("fullscreenchange", () => {
      fullBtn.textContent = document.fullscreenElement ? t("Vollbild beenden") : t("Vollbild");
    });
  }
  window.addEventListener("keydown", (e) => {
    if ((e.key === "f" || e.key === "F") && !e.ctrlKey && !e.metaKey && !e.altKey &&
        !/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName) && document.fullscreenEnabled) {
      e.preventDefault();
      toggleFull();
    }
  });

  // Bild speichern: direkt nach dem Zeichnen auslesen, solange der Puffer noch gefüllt ist
  document.getElementById("view-png")?.addEventListener("click", () => {
    controls.update();
    draw();
    const url = canvas.toDataURL("image/png");
    const a = document.createElement("a");
    const name = (root.dataset.name || "druckarchiv").replace(/[\\/:*?"<>|]+/g, "_").trim() || "druckarchiv";
    a.href = url;
    a.download = `${name} – ${t("Platte {n}", { n: plate })}.png`;
    document.body.append(a);
    a.click();
    a.remove();
  });
  document.getElementById("view-top").addEventListener("click", () => { topView(); render(); });

  // Rendern nur bei Bedarf, das schont Akku und Lüfter
  let pending = false;
  function render() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      if (controls.update()) render(); // Dämpfung ausklingen lassen
      draw();
    });
  }

  // Tiefenschattierung: nur im Stillstand, nur bei Röhren und wenn das Gerät es schafft
  const aoToggle = document.getElementById("ao-toggle");
  let aoWanted = store.get("druckarchiv-ao", "1") === "1";
  if (aoToggle) {
    aoToggle.checked = aoWanted;
    aoToggle.addEventListener("change", () => {
      aoWanted = aoToggle.checked;
      store.set("druckarchiv-ao", aoWanted ? "1" : "0");
      render();
    });
  }
  const aoTarget = new THREE.WebGLRenderTarget(1, 1, { depthTexture: new THREE.DepthTexture(1, 1) });
  const aoBuffer = new THREE.WebGLRenderTarget(1, 1, { depthBuffer: false });
  const aoMat = makeAoMaterial(aoTarget.depthTexture, aoTarget.texture);
  const blurMat = makeAoBlurMaterial(aoTarget.depthTexture, aoBuffer.texture);
  aoMat.uniforms.radius.value = hw * 6;
  const aoQuad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), aoMat);
  aoQuad.frustumCulled = false;
  const aoScene = new THREE.Scene();
  aoScene.add(aoQuad);
  const aoCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  const bufSize = new THREE.Vector2();
  const aoActive = () =>
    aoWanted && !moving && !playing && tubes.visible && perf.tube !== null &&
    perf.tube * visibleShare() * 2.2 <= REST_MAX_MS;
  function draw() {
    // Von unten (z. B. um die erste Schicht anzusehen) die Platte ausblenden, sie würde alles verdecken
    bedGroup.visible = camera.position.z > 0;
    renderer.render(scene, camera);
    if (!aoActive()) return;
    renderer.getDrawingBufferSize(bufSize);
    if (aoTarget.width !== bufSize.x || aoTarget.height !== bufSize.y) {
      aoTarget.setSize(bufSize.x, bufSize.y);
      aoBuffer.setSize(bufSize.x, bufSize.y);
    }
    // 1. Tiefe und Normalen der Röhren
    const bg = scene.background;
    const extrasShown = Object.values(extras).map((o) => o.visible);
    Object.values(extras).forEach((o) => { o.visible = false; });
    plateMesh.material = plateNormalMat; rim.visible = false; nozzle.visible = false; scene.background = null;
    uniforms.normalPass.value = 1;
    renderer.setRenderTarget(aoTarget);
    renderer.render(scene, camera);
    uniforms.normalPass.value = 0;
    plateMesh.material = plateBasicMat; rim.visible = true; nozzle.visible = playDist !== null; scene.background = bg;
    Object.values(extras).forEach((o, i) => { o.visible = extrasShown[i]; });
    // 2. Schattierung in einen eigenen Puffer
    aoMat.uniforms.proj.value.copy(camera.projectionMatrix);
    aoMat.uniforms.projInv.value.copy(camera.projectionMatrixInverse);
    aoMat.uniforms.pxScale.value = camera.projectionMatrix.elements[5] * bufSize.y / 2;
    aoQuad.material = aoMat;
    renderer.setRenderTarget(aoBuffer);
    renderer.render(aoScene, aoCam);
    // 3. Weichzeichnen und über das Bild legen
    blurMat.uniforms.projInv.value.copy(camera.projectionMatrixInverse);
    blurMat.uniforms.texel.value.set(1 / bufSize.x, 1 / bufSize.y);
    aoQuad.material = blurMat;
    renderer.setRenderTarget(null);
    const auto = renderer.autoClear;
    renderer.autoClear = false;
    renderer.render(aoScene, aoCam);
    renderer.autoClear = auto;
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
    saveCamera();
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

  if (!restoreCamera()) resetView();
  setMode(store.get("druckarchiv-color-mode", "tool"));
  resize();
  loadingText.textContent = t("Darstellung wird abgestimmt");
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
    loadingText.textContent = t("Die Vorschau konnte nicht erstellt werden: {error}", { error: err.message });
  }
})();
