// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Schnuecks
// Stilisierter Drucker um die 3D-Vorschau: aus Grundformen gebaut, ohne Modelldateien und
// ohne Herstellergrafiken. Maße in mm, Platte von (0, 0) bis (bed, bed), vorne ist y < 0.
//
// Wie bei echten CoreXY-Druckern (P1S, P2S, X1) bleibt der Druckkopf oben, und das Bett fährt
// Schicht für Schicht nach unten: Die Vorschau hängt dafür Platte und Druck in eine eigene
// Gruppe, die der Viewer um headLevel − aktuelle Höhe verschiebt.
import * as THREE from "three";

// Druckername aus der Datei -> Bauart (vorerst nur geschlossene CoreXY-Drucker)
export function printerKind(name) {
  const n = (name || "").toLowerCase();
  if (/\b(p2s|p1s|x1|x1c|x1e|h2d|h2s)\b/.test(n)) return "enclosed";
  return null;
}

// Quader mit abgerundeten Kanten (Mittelpunkt im Ursprung)
function roundedBox(w, d, h, r) {
  r = Math.max(0.2, Math.min(r, w / 2 - 0.2, d / 2 - 0.2, h / 2 - 0.2));
  const s = new THREE.Shape();
  const x = -w / 2 + r, y = -d / 2 + r, iw = w - 2 * r, id = d - 2 * r;
  s.moveTo(x, y);
  s.lineTo(x + iw, y);
  s.lineTo(x + iw, y + id);
  s.lineTo(x, y + id);
  s.lineTo(x, y);
  const geo = new THREE.ExtrudeGeometry(s, {
    depth: h - 2 * r, bevelEnabled: true, bevelThickness: r, bevelSize: r, bevelSegments: 3, curveSegments: 4,
  });
  geo.translate(0, 0, -(h - 2 * r) / 2);
  geo.computeVertexNormals();
  return geo;
}

// Weiche Umgebung für Spiegelungen auf Glas und Metall: Raum mit zwei hellen Fensterflächen
function makeEnvironment(renderer) {
  const env = new THREE.Scene();
  env.add(new THREE.Mesh(new THREE.BoxGeometry(10, 10, 10),
    new THREE.MeshBasicMaterial({ color: 0x30363c, side: THREE.BackSide })));
  const panel = (w, h, x, y, z, ry, c) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ color: c, side: THREE.DoubleSide }));
    m.position.set(x, y, z);
    m.rotation.y = ry;
    env.add(m);
  };
  panel(6, 3, 0, 3, -4.9, 0, 0xffffff);
  panel(4, 2, -4.9, 2, 0, Math.PI / 2, 0xbfd4e6);
  panel(10, 1, 0, -4.9, 0, 0, 0x1a1d20);
  const pmrem = new THREE.PMREMGenerator(renderer);
  const tex = pmrem.fromScene(env, 0.04).texture;
  pmrem.dispose();
  return tex;
}

export function buildPrinter(kind, bed, renderer) {
  const envMap = renderer ? makeEnvironment(renderer) : null;
  const group = new THREE.Group();    // fest: Gehäuse, Spindeln, Portal, Druckkopf
  const bedParts = new THREE.Group(); // fährt mit dem Bett: Träger und Arme
  group.name = "printer";
  const mat = (color, extra = {}) =>
    new THREE.MeshStandardMaterial({ color, roughness: 0.6, metalness: 0.1, envMap, envMapIntensity: 0.8, ...extra });
  const shell = mat(0x2f3439, { roughness: 0.75 });
  const shellIn = mat(0x262a2e, { roughness: 0.9, side: THREE.DoubleSide });
  const steel = mat(0xb9c1c8, { metalness: 0.9, roughness: 0.28, envMapIntensity: 1.2 });
  const carbon = mat(0x15181b, { roughness: 0.35, metalness: 0.3 });
  const alu = mat(0x6d757c, { metalness: 0.7, roughness: 0.4 });
  const white = mat(0xe8eaec, { roughness: 0.45 });
  const glass = new THREE.MeshStandardMaterial({
    color: 0x9fb7c6, transparent: true, opacity: 0.12, roughness: 0.02, metalness: 0.1,
    envMap, envMapIntensity: 1.6, depthWrite: false,
  });
  const add = (parent, geo, m, x, y, z) => {
    const mesh = new THREE.Mesh(geo, m);
    mesh.position.set(x, y, z);
    parent.add(mesh);
    return mesh;
  };
  const rb = (parent, w, d, h, r, m, x, y, z) => add(parent, roundedBox(w, d, h, r), m, x, y, z);
  const rod = (parent, len, radius, m, axis, x, y, z) => {
    const mesh = add(parent, new THREE.CylinderGeometry(radius, radius, len, 20), m, x, y, z);
    if (axis === "x") mesh.rotation.z = Math.PI / 2;
    if (axis === "z") mesh.rotation.x = Math.PI / 2;
    return mesh;
  };

  // Höhe der Düse über dem Kammerboden: das Bett fährt von hier bis bed darunter
  const headLevel = bed + 40;
  const x0 = -62, x1 = bed + 62, y0 = -58, y1 = bed + 96;
  const z0 = headLevel - bed - 70, z1 = headLevel + 125;
  const w = x1 - x0, d = y1 - y0, h = z1 - z0, mx = (x0 + x1) / 2, my = (y0 + y1) / 2;
  const wall = 14;

  // Gehäuse: Seiten und Rückwand geschlossen, innen etwas heller abgesetzt
  rb(group, wall, d + wall, h + 90, 6, shell, x0 - wall / 2, my, z0 + h / 2 - 45);
  rb(group, wall, d + wall, h + 90, 6, shell, x1 + wall / 2, my, z0 + h / 2 - 45);
  rb(group, w, wall, h + 90, 4, shell, mx, y1 + wall / 2, z0 + h / 2 - 45);
  const inner = (pw, ph, x, y, z, rx, ry) => {
    const m = add(group, new THREE.PlaneGeometry(pw, ph), shellIn, x, y, z);
    m.rotation.set(rx, ry, 0);
  };
  inner(d, h, x0 + 0.5, my, z0 + h / 2, Math.PI / 2, Math.PI / 2);
  inner(d, h, x1 - 0.5, my, z0 + h / 2, Math.PI / 2, -Math.PI / 2);
  inner(w, h, mx, y1 - 0.5, z0 + h / 2, Math.PI / 2, 0);
  rb(group, w + 2 * wall, d + 2 * wall, 90, 8, shell, mx, my, z0 - 45);             // Sockel mit Elektronik
  add(group, new THREE.PlaneGeometry(w, d), mat(0x1c1f22, { roughness: 0.95 }), mx, my, z0 + 0.5); // Kammerboden
  // Deckel: Rahmen mit Glas
  for (const [ww, dd, x, y] of [[w + 2 * wall, wall, mx, y0 - wall / 2], [w + 2 * wall, wall, mx, y1 + wall / 2],
    [wall, d, x0 - wall / 2, my], [wall, d, x1 + wall / 2, my]]) rb(group, ww, dd, 12, 4, shell, x, y, z1 + 6);
  add(group, new THREE.BoxGeometry(w, d, 4), glass, mx, my, z1 + 8);
  // Front: Rahmen um die Tür, Glastür mit Griff, Bedienfeld im Sockel
  rb(group, wall + 4, wall, h, 4, shell, x0 - 2, y0 - wall / 2, z0 + h / 2);
  rb(group, wall + 4, wall, h, 4, shell, x1 + 2, y0 - wall / 2, z0 + h / 2);
  rb(group, w + 2 * wall, wall, 16, 4, shell, mx, y0 - wall / 2, z1 - 4);
  const door = add(group, new THREE.BoxGeometry(w - 6, 4, h - 22), glass, mx, y0 - wall - 2, z0 + h / 2 - 2);
  door.renderOrder = 2;
  rb(group, 10, 14, h * 0.4, 5, mat(0x1b1e21, { roughness: 0.4 }), x1 - 26, y0 - wall - 10, z0 + h * 0.52);
  rb(group, 120, 6, 62, 5, mat(0x0c0f12, { roughness: 0.2, metalness: 0.2, emissive: 0x16a084, emissiveIntensity: 0.08 }),
    mx, y0 - wall - 1, z0 - 45);
  // Innenbeleuchtung: LED-Leiste vorne oben
  rb(group, w - 60, 10, 6, 3, mat(0xffffff, { emissive: 0xfff2dc, emissiveIntensity: 1.4 }), mx, y0 + 18, z1 - 12);

  // Z-Achse: drei Gewindespindeln (vorne links, vorne rechts, hinten Mitte)
  const screws = [[x0 + 26, 14], [x1 - 26, 14], [mx, y1 - 26]];
  for (const [x, y] of screws) rod(group, h - 10, 4, steel, "z", x, y, z0 + h / 2);

  // Bett (fährt mit): Aluträger unter der Platte, Arme zu den Spindeln
  rb(bedParts, bed + 24, bed + 24, 10, 4, alu, bed / 2, bed / 2, -8.5);
  for (const [x, y] of screws) {
    const dx = x - bed / 2, dy = y - bed / 2, len = Math.hypot(dx, dy);
    const arm = add(bedParts, roundedBox(len, 18, 10, 3), alu, bed / 2 + dx / 2, bed / 2 + dy / 2, -16);
    arm.rotation.z = Math.atan2(dy, dx);
    rod(bedParts, 22, 9, mat(0x2a2e33), "z", x, y, -16);                        // Spindelmutter
  }

  // Bewegungssystem oben: Linearschienen links/rechts (Y), Kohlefaserstangen (X), Druckkopf
  const gz = headLevel + 58;
  for (const x of [x0 + 18, x1 - 18]) {
    rb(group, 16, d - 20, 12, 2, steel, x, my, gz);
    rb(group, 26, d - 10, 8, 3, shell, x, my, gz + 10);
  }
  const gantry = new THREE.Group();
  group.add(gantry);
  rod(gantry, w - 30, 4.2, carbon, "x", 0, -9, 0);
  rod(gantry, w - 30, 4.2, carbon, "x", 0, 9, 0);
  for (const sx of [-1, 1]) rb(gantry, 36, 46, 22, 6, shell, sx * (w / 2 - 22), 0, 2);

  const head = new THREE.Group();
  group.add(head);
  rb(head, 56, 60, 70, 12, white, 0, -2, 44);                              // Gehäuse
  rb(head, 46, 4, 44, 8, mat(0x16191c, { roughness: 0.3 }), 0, -33, 50);   // dunkle Front
  const ledDot = add(head, new THREE.CircleGeometry(3, 16), mat(0xffffff, { emissive: 0x7ce0c8, emissiveIntensity: 1 }), 14, -35.2, 64);
  ledDot.rotation.x = Math.PI / 2;
  const fanHub = new THREE.Group();                                        // Bauteillüfter an der Seite
  fanHub.position.set(30, -4, 36);
  fanHub.rotation.z = Math.PI / 2;
  head.add(fanHub);
  add(fanHub, new THREE.CylinderGeometry(15, 15, 4, 28), mat(0x101214), 0, 0, 0);
  const bladeMat = mat(0x3a4048);
  for (let i = 0; i < 7; i++) {
    const g = new THREE.BoxGeometry(2, 3, 12);
    g.translate(0, 0, 6);
    const blade = add(fanHub, g, bladeMat, 0, 2.6, 0);
    blade.rotation.set(0.35, (i / 7) * Math.PI * 2, 0, "YXZ");
  }
  rb(head, 22, 22, 12, 3, alu, 0, 0, 12);                                  // Heizblock
  const tip = add(head, new THREE.ConeGeometry(3.4, 6, 18), mat(0xcf9a45, { metalness: 0.85, roughness: 0.25 }), 0, 0, 3);
  tip.rotation.x = -Math.PI / 2;                                           // Düse

  // PTFE-Schlauch von hinten oben zum Druckkopf
  const tube = new THREE.Mesh(new THREE.BufferGeometry(), mat(0xf2f4f5, { transparent: true, opacity: 0.8, roughness: 0.3 }));
  group.add(tube);
  const feed = new THREE.Vector3(mx - 40, y1 - 10, z1 - 6);
  const routeTube = (x, y) => {
    const top = new THREE.Vector3(x, y + 4, headLevel + 80);
    const curve = new THREE.CatmullRomCurve3([
      feed, new THREE.Vector3((feed.x + x) / 2, (feed.y + y) / 2 + 20, z1 + 2), top.clone().add(new THREE.Vector3(0, 0, 25)), top,
    ]);
    tube.geometry.dispose();
    tube.geometry = new THREE.TubeGeometry(curve, 40, 2, 10, false);
  };

  // Licht nur für den Drucker; die Druckbahnen haben eigene, unbeleuchtete Materialien
  group.add(new THREE.HemisphereLight(0xffffff, 0x3a4046, 1.1));
  const key = new THREE.DirectionalLight(0xffffff, 1.2);
  key.position.set(bed * 0.2, -bed * 1.5, bed * 2.2);
  group.add(key);
  const led = new THREE.PointLight(0xfff2dc, 1.6, 0, 0);
  led.position.set(mx, y0 + 40, z1 - 30);
  group.add(led);

  let fanSpeed = 0;
  // Druckkopf auf (x, y); die Düsenspitze steht immer auf headLevel
  const setHead = (x, y) => {
    head.position.set(x, y, headLevel);
    gantry.position.set(mx, y, gz);
    routeTube(x, y);
  };
  const park = () => setHead(bed - 25, bed + 45);
  park();
  // Zwischen zwei Bildern: Lüfter laufen beim Drucken hoch und danach aus
  const tick = (dt, printing) => {
    fanSpeed += ((printing ? 1 : 0) - fanSpeed) * Math.min(1, dt * 1.5);
    fanHub.rotateY(fanSpeed * dt * 45);
    return fanSpeed > 0.01;
  };
  // Blick durch die Glastür, etwa auf Augenhöhe vor dem Drucker
  const insideView = (camera, controls) => {
    controls.target.set(mx, bed * 0.55, headLevel - 60);
    camera.position.set(mx, y0 - 330, headLevel + 25);
    controls.update();
  };
  return { group, bedParts, headLevel, setHead, park, tick, insideView };
}
