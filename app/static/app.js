// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Schnuecks
// Übersetzungen kommen aus der Seite (<script id="i18n">), Schlüssel ist der deutsche Text
const I18N = (() => {
  try { return JSON.parse(document.getElementById("i18n").textContent); } catch (e) { return {}; }
})();
const t = (key, vars = {}) => (I18N[key] || key).replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m));
const LOCALE = document.documentElement.lang || "de";

// Umschalten Schrägansicht / Draufsicht
document.querySelectorAll("[data-thumbs]").forEach((fig) => {
  const img = fig.querySelector("img");
  fig.querySelectorAll("[data-view]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const view = btn.dataset.view;
      img.src = view === "top" ? img.dataset.top : img.dataset.iso;
      fig.querySelectorAll("[data-view]").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
    });
  });
});

// Namensfeld nur zeigen, wenn „Neues Projekt“ gewählt ist
document.querySelectorAll("[data-new-toggle]").forEach((sel) => {
  const input = sel.form.querySelector("[name=new_name]");
  const sync = () => { input.hidden = sel.value !== "neu"; };
  sel.addEventListener("change", sync);
  sync();
});

// Upload: Drag & Drop, Dateiliste, Fortschrittsbalken
document.querySelectorAll("[data-upload]").forEach((form) => {
  const zone = form.querySelector("[data-dropzone]");
  const input = form.querySelector("[data-file-input]");
  const list = form.querySelector("[data-file-list]");
  const bar = form.querySelector("[data-progress]");
  const status = form.querySelector("[data-status]");
  const submit = form.querySelector("[data-submit]");

  const mb = (n) => (n / 1048576).toLocaleString(LOCALE, { maximumFractionDigits: 1 }) + " MB";
  const showFiles = () => {
    list.replaceChildren(...[...input.files].map((f) => {
      const li = document.createElement("li");
      li.textContent = `${f.name} (${mb(f.size)})`;
      return li;
    }));
  };
  input.addEventListener("change", showFiles);
  ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("is-over"); }));
  ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove("is-over")));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; showFiles(); }
  });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (!input.files.length) return;
    const xhr = new XMLHttpRequest();
    xhr.open("POST", form.action);
    xhr.upload.addEventListener("progress", (ev) => {
      if (!ev.lengthComputable) return;
      const pct = Math.round((ev.loaded / ev.total) * 100);
      bar.value = pct;
      status.textContent = pct < 100 ? t("{pct} % übertragen", { pct }) : t("Wird eingelesen …");
    });
    xhr.addEventListener("load", () => {
      if (xhr.responseURL && !xhr.responseURL.endsWith("/upload")) {
        window.location.href = xhr.responseURL;
      } else {
        document.open(); document.write(xhr.responseText); document.close();
      }
    });
    xhr.addEventListener("error", () => {
      status.textContent = t("Übertragung fehlgeschlagen. Bitte erneut versuchen.");
      submit.disabled = false;
    });
    submit.disabled = true;
    bar.hidden = false;
    xhr.send(new FormData(form));
  });
});

// Filter sofort anwenden: Auswahlfelder beim Ändern, Suche nach kurzer Tipppause
document.querySelectorAll("[data-autofilter]").forEach((form) => {
  const button = form.querySelector("[data-filter-button]");
  if (button) button.hidden = true;  // ohne JavaScript bleibt der Knopf sichtbar

  form.querySelectorAll("select").forEach((sel) => sel.addEventListener("change", () => form.requestSubmit()));

  const search = form.querySelector("input[type=search]");
  if (!search) return;
  let timer;
  let last = search.value;
  const submitSearch = () => {
    if (search.value === last) return;
    try { sessionStorage.setItem("druckarchiv-search-focus", "1"); } catch (e) {}
    form.requestSubmit();
  };
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(submitSearch, 500); });
  // Nach dem Neuladen weitertippen können
  try {
    if (sessionStorage.getItem("druckarchiv-search-focus")) {
      sessionStorage.removeItem("druckarchiv-search-focus");
      search.focus();
      const end = search.value.length;
      search.setSelectionRange(end, end);
    }
  } catch (e) {}
});

// Sprachmenü in der Titelleiste: schließen bei Klick daneben oder mit Esc
document.querySelectorAll("details[data-menu]").forEach((menu) => {
  document.addEventListener("click", (e) => { if (!menu.contains(e.target)) menu.open = false; });
  menu.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && menu.open) { e.preventDefault(); e.stopPropagation(); menu.open = false; menu.querySelector("summary").focus(); }
  });
});

// Sicherheitsabfrage vor dem Löschen
document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (e) => {
    if (!window.confirm(form.dataset.confirm)) e.preventDefault();
  });
});

// Zurück: kommt man von genau dieser Seite, per Browser-Verlauf zurück
// (Filter und Scrollposition bleiben erhalten). Esc als Abkürzung.
document.querySelectorAll("[data-back]").forEach((link) => {
  link.addEventListener("click", (e) => {
    try {
      const ref = new URL(document.referrer);
      const target = new URL(link.href);
      if (ref.origin === location.origin && ref.pathname === target.pathname && history.length > 1) {
        e.preventDefault();
        history.back();
      }
    } catch (err) { /* kein Referrer: normaler Link */ }
  });
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || e.defaultPrevented) return;
  const el = document.activeElement;
  if (el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))) { el.blur(); return; }
  const back = document.querySelector("[data-back]");
  if (back) back.click();
});

// Plattenleiste: eine Platte anzeigen, per Klick, Adresse (#platte-…) oder Pfeiltasten wechseln
document.querySelectorAll("[data-plate-nav]").forEach((nav) => {
  const links = [...nav.querySelectorAll("[data-plate-link]")];
  const plates = links.map((a) => document.getElementById(a.dataset.plateLink)).filter(Boolean);
  const files = [...document.querySelectorAll("[data-file]")];
  if (plates.length < 2) return;
  let mode = "single";
  try { mode = localStorage.getItem("druckarchiv-plate-mode") || "single"; } catch (e) {}
  let current = plates[0];

  const apply = () => {
    const single = mode === "single";
    nav.classList.toggle("is-all", !single);
    plates.forEach((p) => { p.hidden = single && p !== current; });
    files.forEach((f) => {
      const hasPlates = f.querySelector(".plate");
      f.hidden = single && hasPlates && !f.contains(current);
    });
    links.forEach((a) => a.setAttribute("aria-current", String(a.dataset.plateLink === current.id)));
    nav.querySelectorAll("[data-plate-mode]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.plateMode === mode)));
  };
  const select = (id, scroll) => {
    const p = document.getElementById(id);
    if (!p || !plates.includes(p)) return;
    current = p;
    apply();
    const link = links.find((a) => a.dataset.plateLink === id);
    link?.scrollIntoView({ block: "nearest", inline: "nearest" });
    if (mode === "all" && scroll) p.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  links.forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    history.replaceState(null, "", "#" + a.dataset.plateLink);
    select(a.dataset.plateLink, true);
  }));
  nav.querySelectorAll("[data-plate-mode]").forEach((b) => b.addEventListener("click", () => {
    mode = b.dataset.plateMode;
    try { localStorage.setItem("druckarchiv-plate-mode", mode); } catch (e) {}
    apply();
  }));
  window.addEventListener("hashchange", () => select(location.hash.slice(1), true));
  document.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    const el = document.activeElement;
    if (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return;
    const i = plates.indexOf(current) + (e.key === "ArrowRight" ? 1 : -1);
    if (i < 0 || i >= plates.length) return;
    e.preventDefault();
    history.replaceState(null, "", "#" + plates[i].id);
    select(plates[i].id, true);
  });

  const fromHash = location.hash.slice(1);
  if (fromHash && document.getElementById(fromHash)) current = document.getElementById(fromHash);
  apply();
});
