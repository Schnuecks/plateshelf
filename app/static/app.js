// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Schnuecks
// Translations come from the page (<script id="i18n">); the key is the German text
const I18N = (() => {
  try { return JSON.parse(document.getElementById("i18n").textContent); } catch (e) { return {}; }
})();
const t = (key, vars = {}) => (I18N[key] || key).replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m));
const LOCALE = document.documentElement.lang || "de";

// Toggle angled view / top view
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

// Only show the name field when „Neues Projekt“ is selected
document.querySelectorAll("[data-new-toggle]").forEach((sel) => {
  const input = sel.form.querySelector("[name=new_name]");
  const sync = () => { input.hidden = sel.value !== "neu"; };
  sel.addEventListener("change", sync);
  sync();
});

// Printers: show the fields matching the type (serial number and access code only for Bambu)
document.querySelectorAll("[data-printer-kind]").forEach((sel) => {
  sel.addEventListener("change", () => { sel.closest("[data-printer]").dataset.kind = sel.value; });
});

// Top-bar menu on the right (history, trash, settings, account, sign out)
document.querySelectorAll("[data-nav-menu]").forEach((menu) => {
  const button = menu.querySelector(".nav-menu-btn");
  const set = (open) => { menu.classList.toggle("is-open", open); button.setAttribute("aria-expanded", String(open)); };
  button.addEventListener("click", () => set(!menu.classList.contains("is-open")));
  document.addEventListener("click", (e) => { if (!menu.contains(e.target)) set(false); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") set(false); });
});

// Header on phones: expand/collapse the menu; remember the header height for the 3D preview
document.querySelectorAll("[data-menu-toggle]").forEach((button) => {
  const bar = button.closest(".topbar");
  const set = (open) => {
    bar.classList.toggle("menu-open", open);
    button.setAttribute("aria-expanded", String(open));
  };
  button.addEventListener("click", () => set(!bar.classList.contains("menu-open")));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") set(false); });
});
document.querySelectorAll(".topbar").forEach((bar) => {
  const store = () => document.documentElement.style.setProperty("--topbar-h", `${bar.offsetHeight}px`);
  store();
  if ("ResizeObserver" in window) new ResizeObserver(store).observe(bar);
});

// Settings: show only one section at a time, chosen via the anchor (#printers, #power, #history)
document.querySelectorAll("[data-settings]").forEach((root) => {
  const sections = [...root.querySelectorAll("[data-section]")];
  const links = [...root.querySelectorAll(".settings-nav a")];
  const show = () => {
    const target = sections.find((s) => "#" + s.id === location.hash) || sections[0];
    sections.forEach((s) => { s.hidden = s !== target; });
    links.forEach((a) => a.setAttribute("aria-current", a.hash === "#" + target.id ? "page" : "false"));
  };
  links.forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    history.replaceState(null, "", location.pathname + location.search + a.hash);
    show();
  }));
  window.addEventListener("hashchange", show);
  show();
});

// Toggles save immediately; the button is only for browsers without JavaScript
document.querySelectorAll("[data-autosubmit]").forEach((input) => {
  const button = input.form.querySelector("[data-autosubmit-button]");
  if (button) button.hidden = true;
  input.addEventListener("change", () => input.form.requestSubmit());
});

// Running print in the title bar and in the browser tab title („45 % · …“): refresh every 15 s.
// Also while the tab is hidden, so the progress is visible in the tab strip.
const baseTitle = document.title;
// The server sorts: the print that finishes next comes first.
const tabTitle = (items) => {
  const p = items.find((i) => i.progress != null);
  if (!p) { document.title = baseTitle; return; }
  const pct = Math.round(p.progress);
  document.title = items.length > 1
    ? `${t("{n} Drucke · nächster {pct} %", { n: items.length, pct })} · ${baseTitle}`
    : `${pct} % · ${baseTitle}`;
};
document.querySelectorAll("[data-live-print]").forEach((wrap) => {
  const link = wrap.querySelector("[data-live-link]");
  const text = wrap.querySelector("[data-live-text]");
  const more = wrap.querySelector("[data-live-more]");
  const list = wrap.querySelector("[data-live-list]");
  const setOpen = (open) => { list.hidden = !open; more.setAttribute("aria-expanded", String(open)); };
  more.addEventListener("click", () => setOpen(list.hidden));
  document.addEventListener("click", (e) => { if (!wrap.contains(e.target)) setOpen(false); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") setOpen(false); });
  const refresh = () => {
    fetch("/api/printing", { headers: { Accept: "application/json" } })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data) return;
        const items = data.items || [];
        tabTitle(items);
        wrap.hidden = items.length === 0;
        if (!items.length) return;
        text.textContent = items[0].text;
        link.href = items[0].url;
        more.hidden = items.length < 2;
        more.textContent = "+" + (items.length - 1);
        if (items.length < 2) setOpen(false);
        list.replaceChildren(...items.map((i) => {
          const li = document.createElement("li");
          const a = document.createElement("a");
          a.href = i.url;
          a.textContent = i.text;
          li.append(a);
          return li;
        }));
      })
      .catch(() => {});
  };
  refresh();
  setInterval(refresh, 15000);
});

// Refresh progress of running prints every 15 s: on the project tiles of the overview and in
// the print lists (project page, history). The overview reloads anyway when a print starts or
// ends (see below); this is only about the percentages.
const liveBadges = document.querySelectorAll("[data-live-project], [data-live-job]");
const liveSeen = new Set();
if (liveBadges.length) {
  const fmtPct = (v) => Math.round(v).toLocaleString(document.documentElement.lang || undefined);
  setInterval(() => {
    if (document.hidden) return;
    fetch("/api/printing", { headers: { Accept: "application/json" } })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data) return;
        const items = data.items || [];
        const byProject = new Map();
        items.filter((i) => i.project != null).forEach((i) => {
          const key = String(i.project);
          byProject.set(key, [...(byProject.get(key) || []), i]);
        });
        const byJob = new Map(items.filter((i) => i.job != null).map((i) => [String(i.job), i]));
        let ended = false;
        liveBadges.forEach((badge) => {
          if (badge.dataset.liveJob) {
            const item = byJob.get(badge.dataset.liveJob);
            if (item && item.progress != null) badge.textContent = `${badge.dataset.label} ${fmtPct(item.progress)} %`;
            // Reported a moment ago, not anymore: the print has ended → reload so „Fertig“ is shown.
            // Only if it was already reported on this page, otherwise a stuck entry would reload forever.
            if (item) liveSeen.add(badge.dataset.liveJob);
            else if (liveSeen.has(badge.dataset.liveJob)) ended = true;
            return;
          }
          // Project tile: same project on several printers → „Druckt 2× 45 % / 12 %“
          const jobs = byProject.get(badge.dataset.liveProject) || [];
          if (!jobs.length) return;
          const pcts = jobs.filter((i) => i.progress != null).map((i) => `${fmtPct(i.progress)} %`);
          badge.textContent = [badge.dataset.label, jobs.length > 1 ? `${jobs.length}×` : "", pcts.join(" / ")]
            .filter(Boolean).join(" ");
        });
        const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "") ||
          document.querySelector("details[open] textarea, dialog[open]");
        if (ended && !typing) location.reload();
      })
      .catch(() => {});
  }, 15000);
}

// Overview: show new prints from the printer (or other changes) automatically. Polls every
// 20 s, every 3 s during a printer sync. If something is selected or a field is being
// edited, only a notice appears so nothing gets lost.
document.querySelectorAll("[data-library-version]").forEach((marker) => {
  let version = marker.dataset.libraryVersion;
  const banner = document.querySelector("[data-library-changed]");
  const params = new URLSearchParams(location.search);
  const fresh = () => { params.delete("printers"); return location.pathname + (params.size ? "?" + params : ""); };
  let fastUntil = params.has("printers") || marker.dataset.syncRunning ? Date.now() + 180000 : 0;
  // After „Von Druckern holen“ reload even if nothing new arrived: otherwise button and notice keep
  // showing „wird abgefragt“
  let watchSync = fastUntil > 0;
  const busy = () => document.querySelector("[data-bulk-item]:checked") ||
    /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");
  banner?.querySelector("[data-library-reload]")?.addEventListener("click", (e) => {
    e.preventDefault();
    location.replace(fresh());
  });
  const tick = async () => {
    if (!document.hidden) {
      try {
        const r = await fetch("/api/library", { headers: { Accept: "application/json" } });
        if (r.ok) {
          const data = await r.json();
          if (data.sync_running) fastUntil = Math.max(fastUntil, Date.now() + 30000);
          const synced = watchSync && !data.sync_running && String(data.last_sync ?? "") !== marker.dataset.lastSync;
          if ((data.version !== version || synced) && !busy()) { location.replace(fresh()); return; }
          if (synced) watchSync = false;
          if (data.version !== version) {
            version = data.version;
            if (banner) banner.hidden = false;
          }
        }
      } catch { /* offline: next time */ }
    }
    setTimeout(tick, Date.now() < fastUntil ? 3000 : 20000);
  };
  setTimeout(tick, fastUntil ? 3000 : 20000);
});

// Settings: refresh the dot in front of each printer every 5 s, so you see right away when
// switching it on whether it is reachable. When one comes back, the server syncs; then the page
// reloads with the new details (files, error text).
document.querySelectorAll("[data-printer-live]").forEach((list) => {
  let seen = list.dataset.lastSync;
  let changed = false;  // only reload if something changed on a printer
  const tick = async () => {
    if (!document.hidden) {
      try {
        const r = await fetch("/api/printers/status", { headers: { Accept: "application/json" } });
        if (r.ok) {
          const data = await r.json();
          list.querySelectorAll("[data-printer-dot]").forEach((dot) => {
            const online = data.printers?.[dot.dataset.printerDot];
            if (online === undefined) return;
            if (dot.classList.contains(online ? "is-error" : "is-ok") || dot.classList.contains("is-unknown")) changed = true;
            dot.classList.toggle("is-ok", online);
            dot.classList.toggle("is-error", !online);
            dot.classList.remove("is-unknown");
            dot.title = online ? dot.dataset.online : dot.dataset.offline;
            // The text below comes from the last sync: if it no longer matches the dot, replace it
            // temporarily until the sync delivers the exact details
            const text = list.querySelector(`[data-printer-status="${CSS.escape(dot.dataset.printerDot)}"]`);
            if (text && text.classList.contains(online ? "is-error" : "is-ok")) {
              text.textContent = online ? text.dataset.back : text.dataset.gone;
              text.classList.toggle("is-ok", online);
              text.classList.toggle("is-error", !online);
              text.hidden = false;
            }
          });
          const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "") ||
            list.querySelector("details[open]");
          const last = String(data.last_sync ?? "");
          if (!data.sync_running && last !== seen) {
            if (changed && !typing) { location.replace(location.pathname + "#printers"); return; }
            seen = last;
          }
        }
      } catch { /* offline: next time */ }
    }
    setTimeout(tick, 5000);
  };
  setTimeout(tick, 1500);
});

// Settings after saving the printers: reload as soon as the sync is done, so the
// status (green/red) is visible right away
document.querySelectorAll("[data-sync-watch]").forEach((note) => {
  const until = Date.now() + 180000;
  const tick = async () => {
    try {
      const r = await fetch("/api/library", { headers: { Accept: "application/json" } });
      if (r.ok) {
        const data = await r.json();
        const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");
        if (!data.sync_running && String(data.last_sync ?? "") !== note.dataset.lastSync && !typing) {
          location.replace(location.pathname + "#printers");
          return;
        }
      }
    } catch { /* offline: next time */ }
    if (Date.now() < until) setTimeout(tick, 2000);
  };
  setTimeout(tick, 2000);
});

// Copy diagnostics (clipboard, otherwise select the text)
document.querySelectorAll("[data-copy]").forEach((button) => {
  const section = button.closest("section");
  const source = section.querySelector("[data-copy-source]");
  const done = section.querySelector("[data-copy-done]");
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(source.value);
    } catch {
      source.select();
      document.execCommand("copy");
    }
    done.hidden = false;
    setTimeout(() => { done.hidden = true; }, 2500);
  });
});

// Upload: drag & drop, file list, progress bar
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

// Apply filters immediately: select fields on change, search after a short typing pause
document.querySelectorAll("[data-filter-toggle]").forEach((button) => {
  const form = button.form;
  const set = (open) => { form.classList.toggle("filters-open", open); button.setAttribute("aria-expanded", String(open)); };
  button.addEventListener("click", () => set(!form.classList.contains("filters-open")));
  document.addEventListener("click", (e) => { if (!form.contains(e.target)) set(false); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") set(false); });
});

document.querySelectorAll("[data-autofilter]").forEach((form) => {
  const button = form.querySelector("[data-filter-button]");
  if (button) button.hidden = true;  // without JavaScript the button stays visible

  form.querySelectorAll("select").forEach((sel) => sel.addEventListener("change", () => form.requestSubmit()));

  const search = form.querySelector("input[type=search]");
  if (!search) return;
  let timer;
  let last = search.value;
  const submitSearch = () => {
    if (search.value === last) return;
    try { sessionStorage.setItem("plateshelf-search-focus", "1"); } catch (e) {}
    form.requestSubmit();
  };
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(submitSearch, 500); });
  // Keep typing after the reload
  try {
    if (sessionStorage.getItem("plateshelf-search-focus")) {
      sessionStorage.removeItem("plateshelf-search-focus");
      search.focus();
      const end = search.value.length;
      search.setSelectionRange(end, end);
    }
  } catch (e) {}
});

// Language menu in the title bar: close on outside click or with Esc
document.querySelectorAll("details[data-menu]").forEach((menu) => {
  document.addEventListener("click", (e) => { if (!menu.contains(e.target)) menu.open = false; });
  menu.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && menu.open) { e.preventDefault(); e.stopPropagation(); menu.open = false; menu.querySelector("summary").focus(); }
  });
});

// Confirmation before deleting
// Multi-select in the overview: the bar appears as soon as a project is selected
const bulk = document.querySelector("[data-bulk]");
if (bulk) {
  const items = [...document.querySelectorAll("[data-bulk-item]")];
  const count = bulk.querySelector("[data-bulk-count]");
  const selected = () => items.filter((i) => i.checked).length;
  const update = () => {
    const n = selected();
    bulk.hidden = n === 0;
    document.body.classList.toggle("bulk-active", n > 0);
    count.textContent = (n === 1 ? bulk.dataset.one : bulk.dataset.many).replace("{n}", n);
  };
  items.forEach((i) => i.addEventListener("change", update));
  bulk.querySelector("[data-bulk-clear]").addEventListener("click", () => {
    items.forEach((i) => { i.checked = false; });
    update();
  });
  bulk.addEventListener("submit", (e) => {
    if (e.submitter && e.submitter.value === "trash"
        && !window.confirm(bulk.dataset.trashConfirm.replace("{n}", selected()))) e.preventDefault();
  });
  update();  // the browser restores checkmarks when going back
}

document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (e) => {
    if (!window.confirm(form.dataset.confirm)) e.preventDefault();
  });
});

// Back: if we came from exactly this page, go back via browser history
// (filters and scroll position are kept). Esc as a shortcut.
document.querySelectorAll("[data-back]").forEach((link) => {
  link.addEventListener("click", (e) => {
    try {
      const ref = new URL(document.referrer);
      const target = new URL(link.href);
      if (ref.origin === location.origin && ref.pathname === target.pathname && history.length > 1) {
        e.preventDefault();
        history.back();
      }
    } catch (err) { /* no referrer: normal link */ }
  });
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || e.defaultPrevented || document.fullscreenElement) return;
  const el = document.activeElement;
  if (el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))) { el.blur(); return; }
  const back = document.querySelector("[data-back]");
  if (back) back.click();
});

// Plate strip: show one plate, switch by click, URL (#platte-…) or arrow keys
document.querySelectorAll("[data-plate-nav]").forEach((nav) => {
  const allLinks = [...nav.querySelectorAll("[data-plate-link]")];
  const files = [...document.querySelectorAll("[data-file]")];
  if (allLinks.length < 2) return;
  // Several versions (layer heights): show only the selected one, initially the newest
  const filter = nav.querySelector("[data-version-filter]");
  let version = filter ? filter.querySelector("[data-version]").dataset.version : "*";
  const shown = (el) => version === "*" || !el.dataset.version || el.dataset.version === version;
  let links = allLinks;
  let plates = [];
  const refresh = () => {
    nav.querySelectorAll(".plate-strip > li").forEach((li) => { li.hidden = !shown(li); });
    links = allLinks.filter((a) => shown(a.closest("li")));
    plates = links.map((a) => document.getElementById(a.dataset.plateLink)).filter(Boolean);
  };
  refresh();
  let mode = "single";
  try { mode = localStorage.getItem("plateshelf-plate-mode") || localStorage.getItem("druckarchiv-plate-mode") || "single"; } catch (e) {}
  let current = plates[0];

  const apply = () => {
    const single = mode === "single";
    if (!plates.includes(current)) current = plates[0];
    nav.classList.toggle("is-all", !single);
    plates.forEach((p) => { p.hidden = single && p !== current; });
    files.forEach((f) => {
      const hasPlates = f.querySelector(".plate");
      f.hidden = !shown(f) || (single && hasPlates && !f.contains(current));
    });
    links.forEach((a) => a.setAttribute("aria-current", String(a.dataset.plateLink === current.id)));
    nav.querySelectorAll("[data-plate-mode]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.plateMode === mode)));
  };
  const select = (id, scroll) => {
    const p = document.getElementById(id);
    if (!p) return;
    if (!plates.includes(p) && filter) {  // URL points to another version: switch to it
      const li = allLinks.find((a) => a.dataset.plateLink === id)?.closest("li");
      if (li) { version = li.dataset.version; markVersion(); refresh(); }
    }
    if (!plates.includes(p)) return;
    current = p;
    apply();
    const link = links.find((a) => a.dataset.plateLink === id);
    link?.scrollIntoView({ block: "nearest", inline: "nearest" });
    if (mode === "all" && scroll) p.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  function markVersion() {
    filter?.querySelectorAll("[data-version]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.version === version)));
  }
  if (filter) {
    filter.hidden = false;
    filter.querySelectorAll("[data-version]").forEach((b) => b.addEventListener("click", () => {
      version = b.dataset.version;
      markVersion();
      refresh();
      apply();
    }));
  }

  allLinks.forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    history.replaceState(null, "", "#" + a.dataset.plateLink);
    select(a.dataset.plateLink, true);
  }));
  nav.querySelectorAll("[data-plate-mode]").forEach((b) => b.addEventListener("click", () => {
    mode = b.dataset.plateMode;
    try { localStorage.setItem("plateshelf-plate-mode", mode); } catch (e) {}
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
  if (fromHash && document.getElementById(fromHash)) select(fromHash, false);
  else apply();
});
