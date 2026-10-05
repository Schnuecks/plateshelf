// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Schnuecks
// Stilisierter Drucker um die 3D-Vorschau: aus einfachen Formen gebaut, ohne Modelldateien
// und ohne Herstellergrafiken. Maße in mm, Platte von (0, 0) bis (bed, bed), vorne ist y < 0.
import * as THREE from "three";

// Druckername aus der Datei -> Bauart (vorerst nur geschlossene CoreXY-Drucker)
export function printerKind(name) {
  const n = (name || "").toLowerCase();
  if (/\b(p2s|p1s|x1|x1c|x1e|h2d|h2s)\b/.test(n)) return "enclosed";
  return null;
}

export function buildPrinter(kind, bed) {
  const group = new THREE.Group();
  group.name = "printer";
  const mat = (color, extra = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.65, metalness: 0.15, ...extra });
  const body = mat(0x3b4046);
  const trim = mat(0x4a5057);
  const rail = mat(0x9aa3ab, { metalness: 0.6, roughness: 0.35 });
  const glass = new THREE.MeshStandardMaterial({
    color: 0xa9c2d4, transparent: true, opacity: 0.1, roughness: 0.05, metalness: 0, depthWrite: false,
  });
  const side = new THREE.MeshStandardMaterial({ color: 0x24282c, transparent: true, opacity: 0.5, depthWrite: false });
  const box = (w, d, h, m, x, y, z) => {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, d, h), m);
    mesh.position.set(x, y, z);
    group.add(mesh);
    return mesh;
  };

  // Gehäuse: innen etwas Luft um die Platte, hinten Platz für Technik
  const x0 = -55, x1 = bed + 55, y0 = -50, y1 = bed + 85, z0 = -95, z1 = bed + 125;
  const w = x1 - x0, d = y1 - y0, h = z1 - z0, mx = (x0 + x1) / 2, my = (y0 + y1) / 2;
  const post = 22;
  for (const [px, py] of [[x0, y0], [x1, y0], [x0, y1], [x1, y1]]) box(post, post, h, body, px, py, z0 + h / 2);
  box(w + post, d + post, 70, body, mx, my, z0 - 35);                    // Sockel
  box(w + post, post, post, body, mx, y0, z1);                            // Rahmen oben vorne
  box(w + post, post, post, body, mx, y1, z1);                            // … hinten
  box(post, d + post, post, body, x0, my, z1);                            // … links
  box(post, d + post, post, body, x1, my, z1);                            // … rechts
  box(w, d, 4, glass, mx, my, z1 + 9);                                    // Glasdeckel
  box(4, d, h, side, x0 - post / 2 + 2, my, z0 + h / 2);                  // Seitenwände
  box(4, d, h, side, x1 + post / 2 - 2, my, z0 + h / 2);
  box(w, 4, h, side, mx, y1 + post / 2 - 2, z0 + h / 2);                  // Rückwand
  box(w, 3, h - 10, glass, mx, y0 - post / 2, z0 + h / 2 + 5);            // Glastür
  box(4, 8, h * 0.45, trim, x1 - 30, y0 - post / 2 - 4, z0 + h * 0.55);   // Türgriff
  const screen = box(78, 3, 44, mat(0x0d1114, { emissive: 0x1fa08c, emissiveIntensity: 0.06 }), x0 + 70, y0 - post / 2 - 2, z0 - 35);
  screen.name = "screen";
  box(bed + 20, bed + 20, 14, trim, bed / 2, bed / 2, -12);               // Heizbett unter der Platte

  // Bewegungssystem: Y-Schienen links und rechts, X-Portal und Druckkopf
  const motion = new THREE.Group();
  group.add(motion);
  const rails = [x0 + 14, x1 - 14].map((x) => {
    const r = new THREE.Mesh(new THREE.BoxGeometry(14, d - 30, 14), rail);
    r.position.set(x, my, 0);
    motion.add(r);
    return r;
  });
  const gantry = new THREE.Mesh(new THREE.BoxGeometry(w - 10, 22, 18), rail);
  motion.add(gantry);
  const head = new THREE.Group();
  const cover = new THREE.Mesh(new THREE.BoxGeometry(52, 46, 62), mat(0xdfe2e5, { roughness: 0.5 }));
  cover.position.set(0, -12, 41);
  const front = new THREE.Mesh(new THREE.BoxGeometry(40, 2, 30), mat(0x2a2e33));
  front.position.set(0, -36, 46);
  const fan = new THREE.Mesh(new THREE.CylinderGeometry(12, 12, 3, 24), mat(0x16191c));
  fan.rotation.x = Math.PI / 2;
  fan.position.set(0, -37.5, 46);
  const block = new THREE.Mesh(new THREE.BoxGeometry(18, 18, 10), mat(0xb8bec4, { metalness: 0.5 }));
  block.position.set(0, 0, 9);
  const tip = new THREE.Mesh(new THREE.ConeGeometry(3.2, 5, 16), mat(0xd4a24a, { metalness: 0.7, roughness: 0.3 }));
  tip.rotation.x = -Math.PI / 2;
  tip.position.set(0, 0, 2.5);
  head.add(cover, front, fan, block, tip);
  motion.add(head);

  // Licht nur für den Drucker; die Druckbahnen haben eigene, unbeleuchtete Materialien
  group.add(new THREE.HemisphereLight(0xffffff, 0x50585f, 2.6));
  const sun = new THREE.DirectionalLight(0xffffff, 2.2);
  sun.position.set(bed * 0.3, -bed * 1.2, bed * 2);
  group.add(sun);
  const lamp = new THREE.PointLight(0xfff4e0, 0.6, 0, 0); // Innenbeleuchtung oben
  lamp.position.set(mx, my, z1 - 20);
  group.add(lamp);

  // Düsenspitze an (x, y, z); Portal und Schienen fahren in der Höhe mit
  const setHead = (x, y, z) => {
    head.position.set(x, y, z);
    gantry.position.set(mx, y + 4, z + 64);
    rails.forEach((r) => { r.position.z = z + 64; });
  };
  const park = (top) => setHead(bed - 20, bed + 30, Math.max(top, 0) + 15);
  park(0);

  // Blick wie vor dem Drucker sitzend: auf Höhe der Tür, leicht von oben
  const frontView = (camera, controls) => {
    controls.target.set(mx, my, (z0 - 70 + z1) / 2);
    camera.position.set(mx, y0 - (z1 - z0 + 90) * 1.55, z1 * 0.62);
    controls.update();
  };
  return { group, setHead, park, frontView };
}
