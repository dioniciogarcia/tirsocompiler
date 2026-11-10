/* Tirso Compiler interface. Talks to the Python side through window.pywebview.api. */
(function () {
  "use strict";

  // ---------------------------------------------------------------- bridge
  const ready = new Promise((resolve) => {
    if (window.pywebview && window.pywebview.api) return resolve();
    window.addEventListener("pywebviewready", resolve);
    if (window.__devApi) resolve();
  });

  async function api(name, ...args) {
    await ready;
    if (window.__devApi) return window.__devApi(name, args);
    return window.pywebview.api[name](...args);
  }

  // ---------------------------------------------------------------- helpers
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;
  const initials = (s) => (s || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  const when = (t) => {
    if (!t) return "";
    const d = new Date(t * 1000);
    return d.toLocaleDateString(undefined, { day: "numeric", month: "short" }) + ", " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  };

  function toast(text, kind = "ok") {
    const el = document.createElement("div");
    el.className = "toast " + kind;
    el.textContent = text;
    $("#toasts").appendChild(el);
    setTimeout(() => el.remove(), 4200);
  }

  function confirmBox(title, body, okLabel) {
    return new Promise((resolve) => {
      const m = $("#modal");
      m.innerHTML = `<div class="modal" role="dialog" aria-modal="true"><h2>${esc(title)}</h2><p>${esc(body)}</p>
        <div class="row" style="justify-content:flex-end"><button class="btn" data-a="no">Cancel</button>
        <button class="btn primary" data-a="yes">${esc(okLabel)}</button></div></div>`;
      m.hidden = false;
      $("[data-a=yes]", m).focus();
      m.onclick = (e) => {
        const a = e.target.dataset.a;
        if (a || e.target === m) { m.hidden = true; resolve(a === "yes"); }
      };
    });
  }

  const S = { consoles: [], profiles: [], byId: {}, page: "dashboard", progress: {}, builder: null, libFilter: "all", libSearch: "", sel: new Set() };
  const consoleOf = (id) => S.byId[id];
  const badge = (c) => c ? `<span class="badge" style="--c:${c.color}">${esc(c.short)}</span>` : `<span class="badge">?</span>`;

  // ---------------------------------------------------------------- navigation
  const pages = {};
  async function go(page, opts) {
    S.page = page;
    $$("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
    const main = $("#main");
    main.innerHTML = `<div class="page"></div>`;
    await pages[page](main.firstChild, opts || {});
    main.scrollTop = 0;
  }
  $("#nav").addEventListener("click", (e) => { const p = e.target.dataset.page; if (p) go(p); });

  // events from Python
  let refreshTimer = null;
  window.tirsoEvent = (event, data) => {
    if (event === "rom_progress") {
      S.progress[data.id] = data.pct;
      const bar = document.querySelector(`[data-progress="${data.id}"] i`);
      if (bar) bar.style.width = data.pct + "%";
    } else if (event === "library_changed") {
      clearTimeout(refreshTimer);
      refreshTimer = setTimeout(() => { if (S.page === "library" || S.page === "dashboard") go(S.page); }, 250);
    } else if (event === "install_log" || event === "install_done" || event === "adb_progress") {
      onInstallEvent(event, data);
    } else if (event.startsWith("build_")) {
      onBuildEvent(event, data);
    } else if (event === "batch_changed") {
      if (S.page === "batch" && !(document.activeElement && document.activeElement.id === "bSearch") && $("#modal").hidden) {
        clearTimeout(S.batchTimer);
        S.batchTimer = setTimeout(() => { if (S.page === "batch") go("batch"); }, 400);
      }
    } else if (event === "files_dropped") {
      $("#dropVeil").classList.remove("show");
      onDropped(data.paths);
    }
  };

  // visual drop hint (the actual paths come from Python)
  let dragDepth = 0;
  document.addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; if (S.page !== "bios") $("#dropVeil").classList.add("show"); });
  document.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("#dropVeil").classList.remove("show"); } });
  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("drop", (e) => { e.preventDefault(); dragDepth = 0; $("#dropVeil").classList.remove("show"); });

  async function onDropped(paths) {
    if (S.page === "bios" && paths.length && paths.every((p) => /\.zip$/i.test(p))) return importDriver(paths);
    if (S.page === "bios") return importBios(S.biosConsole || "ps2", paths);
    const res = await api("import_paths", paths);
    reportImport(res);
    if (S.page === "builder") go("builder", { keep: true }); else go("library");
  }

  function reportImport(res) {
    if (!res) return;
    if (res.added) toast(`Added ${plural(res.added, "game")}. Calculating checksums…`);
    if (res.updated) toast(`${plural(res.updated, "game")} already in the library ${res.updated > 1 ? "were" : "was"} read again.`);
    if (res.skipped && res.skipped.length) {
      const first = res.skipped[0];
      toast(`${plural(res.skipped.length, "file")} skipped: ${first.reason}${res.skipped.length > 1 ? " (and others)" : ""}.`, "warn");
    }
    if (!res.added && !res.updated && !(res.skipped || []).length) toast("No game files were found there.", "warn");
  }

  // ================================================================ DASHBOARD
  pages.dashboard = async (el) => {
    const o = await api("get_overview");
    const c = o.counts;
    const byConsole = Object.fromEntries(o.by_console.map((x) => [x.console, x.n]));
    const max = Math.max(1, ...o.by_console.map((x) => x.n));
    const shelf = S.consoles.map((k) => {
      const n = byConsole[k.id] || 0;
      const h = n ? 34 + Math.round((n / max) * 96) : 34;
      return `<div class="shelf-item"><div class="shelf-spine ${n ? "" : "empty"}" style="--c:${k.color};height:${h}px">${n || ""}</div></div>`;
    }).join("");
    const shelfLabels = S.consoles.map((k) => `<div class="shelf-item"><div class="shelf-label">${esc(k.short)}</div></div>`).join("");

    el.innerHTML = `
      <div class="page-head"><div><h1>Your game shelf</h1>
        <p class="lede">${c.roms ? `${plural(c.roms, "game")} in your library${c.hashing ? `, ${c.hashing} still being checked` : ""}.` : "Add your first game to start."}</p></div>
        <button class="btn primary" id="newGame">New game app</button></div>
      <div class="grid-2">
        <div class="panel">
          <div class="shelf">${shelf}</div>
          <div class="row" style="gap:10px;padding:0 4px">${shelfLabels}</div>
          <div class="figures">
            <div class="figure"><b>${c.roms}</b><span>games in library</span></div>
            <div class="figure"><b>${c.games}</b><span>game apps set up</span></div>
            <div class="figure"><b>${c.apks}</b><span>APKs generated</span></div>
            <div class="figure"><b>${c.engines_ready}/${c.engines_total}</b><span>engines ready</span></div>
          </div>
        </div>
        <div class="stack">
          <div class="panel"><h2>Recently added</h2>
            ${o.recent_roms.length ? `<div class="list">${o.recent_roms.map((r) => `
              <div class="list-row">${badge(consoleOf(r.console))}<div class="grow ellipsis"><b>${esc(r.display_name)}</b></div>
              <span class="faint small">${esc(r.size_h)}</span></div>`).join("")}</div>`
              : `<div class="empty"><h3>No games yet</h3>Add game files you own in the ROM Library, or drag them onto this window.</div>`}
          </div>
          <div class="panel"><h2>What's next</h2>
            <div class="list">
              <div class="list-row"><span class="chip ok"><span class="dot"></span>Done</span><div class="grow">Library, BIOS Manager and game setup</div></div>
              <div class="list-row"><span class="chip ok"><span class="dot"></span>Done</span><div class="grow">Nintendo 64, DS, GBA, PSP, PS2, GameCube and Wii APKs, tested on your phones and TV</div></div>
              <div class="list-row"><span class="chip ok"><span class="dot"></span>Done</span><div class="grow">Install on phones and TVs from Tirso (USB or Wi-Fi)</div></div>
              <div class="list-row"><span class="chip ok"><span class="dot"></span>Done</span><div class="grow">PlayStation 2 (ARMSX2) with the Tirso look and custom GPU drivers</div></div>
              <div class="list-row"><span class="chip ok"><span class="dot"></span>Done</span><div class="grow">GameCube and Wii (Dolphin, Vulkan) and Game Boy Advance</div></div>
              <div class="list-row"><span class="chip ok"><span class="dot"></span>Done</span><div class="grow">Windows installer</div></div>
              <div class="list-row"><span class="chip">Later</span><div class="grow">PlayStation 1 and PlayStation 3</div></div>
            </div>
          </div>
        </div>
      </div>`;
    $("#newGame", el).onclick = () => go("builder", { fresh: true });
  };

  // ================================================================ ROM LIBRARY
  pages.library = async (el) => {
    const roms = await api("list_roms");
    const counts = {};
    roms.forEach((r) => { const k = r.console || "unknown"; counts[k] = (counts[k] || 0) + 1; });
    const filters = [["all", "All", roms.length], ...S.consoles.filter((c) => counts[c.id]).map((c) => [c.id, c.short, counts[c.id]])];
    if (counts.unknown) filters.push(["unknown", "Needs a console", counts.unknown]);

    el.innerHTML = `
      <div class="page-head"><div><h1>ROM Library</h1>
        <p class="lede">Games you own, identified from their file contents. Your files stay where they are; Tirso only reads them.</p></div>
        <div class="row"><button class="btn" id="addFolder">Add folder</button><button class="btn primary" id="addFiles">Add game files</button></div></div>
      ${roms.length ? `
      <div class="toolbar">
        <div class="filters">${filters.map(([k, label, n]) => `<button class="filter ${S.libFilter === k ? "on" : ""}" data-f="${k}">${esc(label)} <span class="faint">${n}</span></button>`).join("")}</div>
        <span class="spacer"></span>
        <input class="search" id="search" placeholder="Search by name or code" value="${esc(S.libSearch)}">
        <button class="btn ghost" id="recheck" title="Look for moved or deleted files">Re-check files</button>
        ${roms.some((r) => r.status === "missing") ? `<button class="btn ghost danger" id="rmMissing" title="Remove the games whose files are gone">Remove missing (${roms.filter((r) => r.status === "missing").length})</button>` : ""}
        <button class="btn primary" id="batchBtn" hidden>Build selected</button>
      </div>
      <p class="hint" id="selHint" style="margin:-6px 0 10px" hidden></p>
      <div class="panel" style="padding:8px 10px"><div class="table-wrap"><table>
        <thead><tr><th style="width:28px"><input type="checkbox" id="selAll" aria-label="Select all shown games"></th><th style="width:34%">Game</th><th>Console</th><th>Region</th><th>Size</th><th>Checksum</th><th>Status</th><th></th></tr></thead>
        <tbody id="romRows"></tbody></table></div></div>`
      : `<div class="drop-zone"><div class="grow"><h3 style="color:var(--text)">Your library is empty</h3>
          Drag game files or a whole folder onto this window, or use Add game files. Supported: ${esc(S.allExt.join(", "))}.</div></div>`}`;

    $("#addFiles", el).onclick = async () => reportImport(await api("pick_rom_files"));
    $("#addFolder", el).onclick = async () => reportImport(await api("pick_rom_folder"));
    if (!roms.length) return;

    $$(".filter", el).forEach((b) => b.onclick = () => { S.libFilter = b.dataset.f; go("library"); });
    if ($("#rmMissing", el)) $("#rmMissing", el).onclick = async () => {
      let r = await api("remove_missing_roms", false);
      if (r.needs_confirm) {
        if (!await confirmBox(`Remove ${plural(r.count, "missing game")}?`, `${plural(r.projects, "project")} in My Games use${r.projects === 1 ? "s" : ""} them and will be removed too. Apps you already built stay where they are.`, "Remove")) return;
        r = await api("remove_missing_roms", true);
      }
      toast(r.removed ? `Removed ${plural(r.removed, "missing game")}.` : "No missing games.");
      go("library");
    };
    $("#recheck", el).onclick = async () => { const r = await api("recheck_roms"); toast(r.changed ? `${plural(r.changed, "file")} changed status.` : "All files are where they were."); go("library"); };
    const draw = () => {
      const q = S.libSearch.toLowerCase();
      const shown = roms.filter((r) => (S.libFilter === "all" || (r.console || "unknown") === S.libFilter)
        && (!q || (r.display_name + " " + (r.serial || "") + " " + r.filename).toLowerCase().includes(q)));
      $("#romRows").innerHTML = shown.map(romRow).join("") || `<tr><td colspan="8" class="muted">No games match.</td></tr>`;
      bindRomRows();
      const all = $("#selAll", el);
      all.checked = shown.length > 0 && shown.every((r) => S.sel.has(r.id));
      all.onchange = () => { shown.forEach((r) => all.checked ? S.sel.add(r.id) : S.sel.delete(r.id)); draw(); };
      $$("[data-sel]", el).forEach((cb) => cb.onchange = () => { cb.checked ? S.sel.add(+cb.dataset.sel) : S.sel.delete(+cb.dataset.sel); updateSel(); });
      updateSel();
    };
    // Several games of one console can be built in one go.
    const updateSel = () => {
      for (const id of [...S.sel]) if (!roms.some((r) => r.id === id)) S.sel.delete(id);
      const picked = roms.filter((r) => S.sel.has(r.id));
      const btn = $("#batchBtn", el), hint = $("#selHint", el);
      const kinds = [...new Set(picked.map((r) => r.console || "unknown"))];
      const c = kinds.length === 1 ? consoleOf(kinds[0]) : null;
      btn.hidden = !picked.length;
      btn.textContent = `Build ${plural(picked.length, "game")}`;
      let problem = "";
      if (picked.length && kinds.length > 1) problem = "Pick games of one console to build them together.";
      else if (picked.length && !c) problem = "Set the console of the selected games first.";
      else if (picked.length && !c.buildable) problem = `${c.name} APKs can't be built yet.`;
      else if (picked.some((r) => r.status !== "ready")) problem = "Some selected games are still being checked or are missing.";
      btn.disabled = !!problem;
      hint.hidden = !problem; hint.textContent = problem;
      btn.onclick = () => openBatch(picked, c);
    };
    $("#search", el).oninput = (e) => { S.libSearch = e.target.value; draw(); };
    draw();
  };

  async function openBatch(picked, c) {
    const games = await api("list_games");
    const B = { profile: "balanced", widescreen: true, resolution: "auto", tv: false, ds_layout: "rotate", wii_controls: "sideways", orientation: "landscape" };
    const feats = c.features || [];
    const m = $("#modal");
    const nameOf = (r) => (games.find((g) => g.rom_id === r.id) || {}).app_name || r.display_name;
    m.innerHTML = `<div class="modal wide" role="dialog" aria-modal="true"><h2>Build ${plural(picked.length, c.name + " game")}</h2>
      <p class="small">Each game becomes its own app with these settings. Names can be changed here; icons are made from the names (pick your own image later in My Games and build again).</p>
      <div class="list" style="margin:10px 0 16px">${picked.map((r) => `<div class="list-row">${badge(c)}<input class="input grow" data-bname="${r.id}" maxlength="50" value="${esc(nameOf(r))}">
        <span class="faint small">${esc(r.size_h)}</span></div>`).join("")}</div>
      <h3>Performance profile</h3>
      <div class="seg" id="bProfile">${S.profiles.map((p) => `<button data-p="${p.id}" class="${B.profile === p.id ? "on" : ""}">${esc(p.name)}</button>`).join("")}</div>
      ${(c.resolutions || []).length ? `<div id="bResWrap" hidden><h3 style="margin-top:14px">Resolution</h3><div class="seg" id="bRes">${c.resolutions.map((x) => `<button data-r="${x.id}" class="${x.id === "auto" ? "on" : ""}" title="${esc(x.detail)}">${esc(x.label)}</button>`).join("")}</div></div>` : ""}
      ${c.screen_layouts ? `<h3 style="margin-top:14px">DS screen layout</h3><select class="select input" id="bLayout">${c.screen_layouts.map((l) => `<option value="${l.id}">${esc(l.name)}</option>`).join("")}</select>`
        : `<h3 style="margin-top:14px">Screen orientation</h3><div class="seg" id="bOrient">${[["landscape", "Landscape"], ["portrait", "Portrait"], ["auto", "Follow the phone"]].map(([k, t]) => `<button data-o="${k}" class="${k === "landscape" ? "on" : ""}">${t}</button>`).join("")}</div>`}
      ${c.controls ? `<h3 style="margin-top:14px">Wii controller</h3><select class="select input" id="bControls">${c.controls.map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join("")}</select>
        <p class="hint">The same for every game in this list. Games played differently can be changed in My Games afterwards.</p>` : ""}
      ${feats.includes("widescreen") ? `<label class="toggle-row" style="margin-top:14px" for="bWide"><span class="grow"><h3>Widescreen hack</h3></span><input type="checkbox" id="bWide" class="switch" checked></label>` : ""}
      ${feats.includes("tv") ? `<label class="toggle-row" for="bTv"><span class="grow"><h3>TV support</h3><p class="small">Also runs on Android TV / Fire TV.</p></span><input type="checkbox" id="bTv" class="switch"></label>` : ""}
      <div class="row" style="justify-content:flex-end;margin-top:18px"><button class="btn" data-a="no">Cancel</button><button class="btn primary" id="bGo">Build ${plural(picked.length, "APK")}</button></div></div>`;
    m.hidden = false;
    const seg = (id, key, after) => $$(`#${id} button`, m).forEach((b) => b.onclick = () => { B[key] = b.dataset[Object.keys(b.dataset)[0]]; $$(`#${id} button`, m).forEach((x) => x.classList.toggle("on", x === b)); if (after) after(); });
    seg("bProfile", "profile", () => { const w = $("#bResWrap", m); if (w) w.hidden = B.profile !== "quality"; });
    seg("bRes", "resolution"); seg("bOrient", "orientation");
    m.onclick = (e) => { if (e.target.dataset.a === "no" || e.target === m) m.hidden = true; };
    $("#bGo", m).onclick = async () => {
      const names = {}; $$("[data-bname]", m).forEach((i) => names[i.dataset.bname] = i.value.trim());
      if (Object.values(names).some((n) => !n)) return toast("Every game needs an app name.", "warn");
      const options = { profile: B.profile, orientation: B.orientation, resolution: B.profile === "quality" ? B.resolution : "auto",
        widescreen: $("#bWide", m) ? $("#bWide", m).checked : false, tv: $("#bTv", m) ? $("#bTv", m).checked : false,
        ds_layout: $("#bLayout", m) ? $("#bLayout", m).value : undefined, wii_controls: $("#bControls", m) ? $("#bControls", m).value : undefined };
      $("#bGo", m).disabled = true;
      const r = await api("batch_build", { rom_ids: picked.map((x) => x.id), names, options });
      m.hidden = true;
      const bad = (r.results || []).filter((x) => !x.ok);
      if (r.queued) toast(`${plural(r.queued, "build")} queued.` + (bad.length ? ` ${bad.length} skipped: ${bad[0].error}` : ""), bad.length ? "warn" : "ok");
      else toast(r.error || "Nothing could be queued.", "fail");
      if (r.queued) { S.sel.clear(); go("builds"); }
    };
  }

  function romRow(r) {
    const c = consoleOf(r.console);
    const options = [`<option value="">Choose console…</option>`, ...S.consoles.map((k) => `<option value="${k.id}" ${k.id === r.console ? "selected" : ""}>${esc(k.name)}</option>`)].join("");
    const consoleCell = r.console_source === "header"
      ? `${badge(c)}`
      : `<select class="select" data-console="${r.id}">${options}</select>${r.console ? "" : `<div class="hint" style="margin-top:4px">Couldn't tell from the file</div>`}`;
    let status;
    if (r.status === "hashing") status = `<div class="progress" data-progress="${r.id}"><i style="width:${S.progress[r.id] || 0}%"></i></div>`;
    else if (r.status === "ready" && r.duplicate_of.length) status = `<span class="chip warn" title="Same contents as another file in your library"><span class="dot"></span>Duplicate</span>`;
    else if (r.status === "ready") status = `<span class="chip ok"><span class="dot"></span>Ready</span>`;
    else if (r.status === "missing") status = `<span class="chip fail" title="${esc(r.path)}"><span class="dot"></span>File missing</span>`;
    else status = `<span class="chip fail" title="${esc(r.message)}"><span class="dot"></span>Unreadable</span>`;
    return `<tr>
      <td><input type="checkbox" data-sel="${r.id}" ${S.sel.has(r.id) ? "checked" : ""} aria-label="Select ${esc(r.display_name)}"></td>
      <td><div class="spine" style="--c:${r.color}"><input class="name-input" data-name="${r.id}" value="${esc(r.display_name)}" aria-label="Game name">
        <div class="faint small ellipsis" title="${esc(r.path)}">${esc(r.serial ? r.serial + "  ·  " : "")}${esc(r.filename)}</div></div></td>
      <td>${consoleCell}</td>
      <td class="muted">${esc(r.region || "Unknown")}</td>
      <td class="muted">${esc(r.size_h)}</td>
      <td class="mono faint" title="${r.sha1 ? "SHA-1 " + esc(r.sha1) : ""}">${r.crc32 ? "CRC " + esc(r.crc32.toUpperCase()) : "…"}</td>
      <td>${status}</td>
      <td style="text-align:right"><button class="btn ghost small danger" data-remove="${r.id}" aria-label="Remove from library">Remove</button></td></tr>`;
  }

  function bindRomRows() {
    $$("[data-name]").forEach((inp) => {
      const save = async () => { const r = await api("rename_rom", +inp.dataset.name, inp.value); if (!r.ok) toast(r.error, "fail"); };
      inp.onchange = save;
      inp.onkeydown = (e) => { if (e.key === "Enter") inp.blur(); };
    });
    $$("[data-console]").forEach((s) => s.onchange = async () => { await api("set_rom_console", +s.dataset.console, s.value); toast("Console saved."); go("library"); });
    $$("[data-remove]").forEach((b) => b.onclick = async () => {
      if (!(await confirmBox("Remove from library?", "The game file on your PC is not deleted. Only Tirso's entry is removed.", "Remove"))) return;
      let r = await api("remove_rom", +b.dataset.remove, false);
      if (r.needs_confirm) {
        if (!(await confirmBox(`Also remove ${plural(r.projects, "project")}?`, "This game is used in My Games. Its project will be removed too. Apps you already built stay where they are.", "Remove both"))) return;
        r = await api("remove_rom", +b.dataset.remove, true);
      }
      if (r.ok) { toast("Removed from library."); go("library"); } else toast(r.error, "fail");
    });
  }

  // ================================================================ GAME BUILDER
  const STEPS = [["console", "Console", "Pick the system"], ["rom", "Game", "Choose the file"], ["check", "Check", "Compatibility"], ["configure", "App", "Name and icon"], ["profile", "Profile", "Performance"]];

  function freshBuilder() {
    return { step: 0, console: null, rom_id: null, report: null, app_name: "", package_id: "", pkgTouched: false, orientation: "landscape", profile: "balanced", widescreen: true, resolution: "auto", tv: false, ds_layout: "rotate", wii_controls: "sideways", graphics: "vulkan", icon_path: null, icon_url: null, bios_id: null, id: null };
  }

  pages.builder = async (el, opts) => {
    if (opts.fresh || !S.builder) S.builder = freshBuilder();
    if (opts.edit) {
      const g = opts.edit;
      S.builder = Object.assign(freshBuilder(), { id: g.id, console: g.console, rom_id: g.rom_id, app_name: g.app_name, package_id: g.package_id, pkgTouched: true, orientation: g.orientation, profile: g.profile, icon_path: g.icon_path, icon_url: g.icon, bios_id: g.bios_id, widescreen: !!(g.settings || {}).widescreen, resolution: (g.settings || {}).resolution || "auto", ds_layout: (g.settings || {}).ds_layout || "stacked", wii_controls: (g.settings || {}).wii_controls || "sideways", graphics: (g.settings || {}).graphics || "vulkan", tv: !!(g.settings || {}).tv, info_dir: g.info_dir || null, step: 3 });
    }
    const B = S.builder;
    const done = [!!B.console, !!B.rom_id, !!(B.report && B.report.can_save), !!(B.app_name && B.package_id) && B.step > 3, false];
    el.innerHTML = `
      <div class="page-head"><div><h1>${B.id ? "Edit game app" : "Game Builder"}</h1>
        <p class="lede">Turn one game you own into its own Android app, step by step.</p></div>
        ${B.id || B.console ? `<button class="btn ghost" id="restart">Start over</button>` : ""}</div>
      <div class="steps">${STEPS.map(([k, t, s], i) => `<button class="step ${i === B.step ? "current" : ""} ${done[i] && i !== B.step ? "done" : ""}" data-step="${i}" ${i > 0 && !done.slice(0, i).every(Boolean) ? "disabled" : ""}><b>${i + 1}. ${t}</b><span>${s}</span></button>`).join("")}</div>
      <div id="stepBody"></div>`;
    $$(".step", el).forEach((b) => b.onclick = () => { B.step = +b.dataset.step; go("builder"); });
    const rs = $("#restart", el); if (rs) rs.onclick = () => go("builder", { fresh: true });
    const body = $("#stepBody", el);
    await [stepConsole, stepRom, stepCheck, stepConfigure, stepProfile][B.step](body, B);
  };

  function next(B) { B.step++; go("builder"); }

  function stepConsole(body, B) {
    body.innerHTML = `<div class="console-grid">${S.consoles.map((c) => `
      <button class="console-card ${B.console === c.id ? "selected" : ""} ${c.phase >= 7 ? "later" : ""}" style="--c:${c.color}" data-c="${c.id}" ${c.phase >= 7 ? "disabled" : ""}>
        <span class="logo"><img src="img/consoles/${c.id}.png" alt="" onerror="this.replaceWith(Object.assign(document.createElement('b'),{textContent:'${esc(c.short)}'}))"></span>
        <span class="body"><span class="grow"><h3>${esc(c.name)}</h3><span class="muted small">${esc(c.engine)}</span></span>
        <span class="chip ${c.buildable && c.tested ? "ok" : c.buildable ? "warn" : ""}">${c.buildable ? (c.tested ? "Ready" : "Ready to test") : esc(c.status)}</span></span>
      </button>`).join("")}</div>
      <p class="muted small" style="margin-top:14px">You can set up a game for any console now. Consoles marked Ready can make APKs today; the others save your setup until their engine arrives.</p>`;
    $$(".console-card", body).forEach((b) => b.onclick = () => {
      if (B.console !== b.dataset.c) { B.rom_id = null; B.report = null; }
      B.console = b.dataset.c; next(B);
    });
  }

  async function stepRom(body, B) {
    const roms = (await api("list_roms")).filter((r) => r.console === B.console);
    const c = consoleOf(B.console);
    body.innerHTML = `
      <div class="row" style="margin-bottom:14px"><h2 style="margin:0">${esc(c.name)} games in your library</h2><span class="spacer"></span>
        <button class="btn" id="importHere">Add game files</button></div>
      ${roms.length ? `<div class="rom-pick">${roms.map((r) => `
        <button class="rom-option ${B.rom_id === r.id ? "selected" : ""}" data-r="${r.id}">
          <div class="spine grow" style="--c:${r.color}"><b>${esc(r.display_name)}</b>
          <div class="faint small">${esc([r.serial, r.region, r.size_h, r.filename].filter(Boolean).join("  ·  "))}</div></div>
          ${r.status === "ready" ? `<span class="chip ok"><span class="dot"></span>Ready</span>` : r.status === "hashing" ? `<span class="chip">Checking…</span>` : `<span class="chip fail">${esc(r.status)}</span>`}
        </button>`).join("")}</div>`
      : `<div class="empty"><h3>No ${esc(c.name)} games yet</h3>Add game files you own (${esc(c.extensions.join(", "))}) or drag them onto this window. Files whose console can't be read appear in the ROM Library, where you can set the console by hand.</div>`}`;
    $("#importHere", body).onclick = async () => { reportImport(await api("pick_rom_files")); go("builder"); };
    $$(".rom-option", body).forEach((b) => b.onclick = () => {
      const r = roms.find((x) => x.id === +b.dataset.r);
      if (B.rom_id !== r.id) {
        if (B.info && B.info.icon_path && B.icon_path === B.info.icon_path) { B.icon_path = null; B.icon_url = null; }
        B.info = null; B.infoRom = null; B.info_dir = undefined; B.nameTouched = false;
      }
      B.rom_id = r.id; B.report = null;
      if (!B.id) { B.app_name = r.display_name.slice(0, 50); B.pkgTouched = false; B.package_id = ""; }
      next(B);
    });
  }

  async function stepCheck(body, B) {
    B.report = await api("check_compat", B.rom_id, B.console);
    const marks = { ok: "✓", warn: "!", fail: "✕", info: "i" };
    body.innerHTML = `<div class="panel"><div class="row" style="margin-bottom:6px"><h2 style="margin:0">Compatibility report</h2><span class="spacer"></span>
      ${B.report.can_save ? `<button class="btn primary" id="contTop">Continue</button>` : ""}</div><div class="checks">${B.report.checks.map((k) => `
      <div class="check ${k.level}"><span class="mark">${marks[k.level]}</span><span>${esc(k.text)}</span></div>`).join("")}</div>
      <div class="builder-foot"><button class="btn ghost" id="again">Check again</button><span class="spacer"></span>
      ${B.report.can_save ? `<button class="btn primary" id="cont">Continue</button>` : `<span class="hint bad">Fix the items marked ✕ to continue.</span>`}</div></div>`;
    $("#again", body).onclick = () => go("builder");
    const cont = $("#cont", body); if (cont) cont.onclick = () => next(B);
    const contTop = $("#contTop", body); if (contTop) contTop.onclick = () => next(B);
  }

  // Game information for single games: details, description, pictures and a logo icon, like Batch Build.
  async function builderInfo(body, B) {
    const box = $("#infoBox", body);
    if (!box) return;
    const show = () => {
      const i = B.info;
      if (!i) return;
      if (!i.ok) {
        box.innerHTML = `<span class="${i.not_found ? "" : "bad"}">${esc(i.error)}</span>
          <div class="row" style="margin-top:8px"><button class="btn small" id="infoFind">Find the game</button>${i.not_found ? "" : `<button class="btn ghost small" id="infoRetry">Try again</button>`}</div>`;
      } else {
        const usingLogo = B.icon_path && B.icon_path === i.icon_path;
        box.innerHTML = `<div style="display:grid;grid-template-columns:${i.pictures.box ? "64px " : ""}minmax(0,1fr);gap:12px;align-items:start">
          ${i.pictures.box ? `<img src="${i.pictures.box}" alt="Box art" style="width:64px;border-radius:4px">` : ""}
          <div style="min-width:0"><b style="color:var(--text)">${esc(i.title)}</b>
            <div class="faint">${esc([i.year, i.developer, i.genre].filter(Boolean).join(" · "))}</div>
            <div style="margin-top:4px;max-height:3.2em;overflow:hidden">${esc(i.description || "No description found.")}</div>
            <label class="row" style="gap:8px;margin-top:8px;cursor:pointer"><input type="checkbox" id="infoUse" ${B.info_dir ? "checked" : ""}> Put this information in the app (pause menu: Game information)</label>
            <div class="row" style="gap:6px;margin-top:8px;flex-wrap:wrap">
              ${i.icon_path && !usingLogo ? `<button class="btn small" id="infoIcon">Use ${i.icon_kind === "logo" ? "logo" : "box art"} icon</button>` : ""}
              ${B.app_name.trim() !== i.title ? `<button class="btn small" id="infoName">Name it “${esc(i.title)}”</button>` : ""}
              <button class="btn ghost small" id="infoFind">Wrong game?</button></div></div></div>`;
      }
      const f = $("#infoFind", body); if (f) f.onclick = () => builderFindGame(B);
      const r = $("#infoRetry", body); if (r) r.onclick = () => { B.info = null; B.infoRom = null; go("builder"); };
      const u = $("#infoUse", body); if (u) u.onchange = () => { B.info_dir = u.checked ? B.info.info_dir : null; };
      const ic = $("#infoIcon", body); if (ic) ic.onclick = () => { B.icon_path = i.icon_path; B.icon_url = i.pictures.icon; go("builder"); };
      const nm = $("#infoName", body); if (nm) nm.onclick = () => { B.app_name = i.title; B.nameTouched = true; if (!B.pkgTouched) B.package_id = ""; go("builder"); };
    };
    if (B.info && B.infoRom === B.rom_id) return show();
    const rom = B.rom_id;
    const res = await api("lookup_rom_info", rom, B.infoName || null);
    if (B.rom_id !== rom || S.page !== "builder") return;
    B.info = res; B.infoRom = rom; B.infoName = null;
    if (res.ok && B.info_dir === undefined) {
      B.info_dir = res.info_dir;
      // new game: use the game's real name and its logo icon unless you already chose your own
      let changed = false;
      if (!B.id && !B.icon_path && res.icon_path) { B.icon_path = res.icon_path; B.icon_url = res.pictures.icon; changed = true; }
      if (!B.id && !B.nameTouched && res.title && B.app_name !== res.title) { B.app_name = res.title; if (!B.pkgTouched) B.package_id = await api("suggest_package", res.title); changed = true; }
      if (changed) return go("builder");
    }
    if ($("#infoBox", body)) show();
  }

  function builderFindGame(B) {
    const m = $("#modal");
    m.innerHTML = `<div class="modal wide" role="dialog" aria-modal="true"><h2>Which game is this?</h2>
      <label class="small faint" for="fixIn">Search the ${esc(consoleOf(B.console).short)} game list</label>
      <input class="search" id="fixIn" style="width:100%;margin-top:4px" value="${esc((B.app_name || "").replace(/:/g, ""))}">
      <div id="fixList" class="list" style="margin-top:10px;max-height:46vh;overflow-y:auto"></div>
      <div class="row" style="justify-content:flex-end;margin-top:16px"><button class="btn" data-a="no">Cancel</button></div></div>`;
    m.hidden = false;
    const input = $("#fixIn", m);
    let t = null;
    const search = async () => {
      const names = await api("search_game_names", B.console, input.value);
      $("#fixList", m).innerHTML = names.map((n) => `<div class="list-row"><div class="grow">${esc(n)}</div><button class="btn small" data-pick="${esc(n)}">This one</button></div>`).join("")
        || `<div class="faint" style="padding:10px 0">No games found. Try fewer words.</div>`;
    };
    input.oninput = () => { clearTimeout(t); t = setTimeout(search, 250); };
    input.focus(); search();
    m.onclick = (e) => {
      if (e.target.dataset.pick) { m.hidden = true; B.info = null; B.infoRom = null; B.infoName = e.target.dataset.pick; B.info_dir = undefined; go("builder"); return; }
      if (e.target.dataset.a === "no" || e.target === m) m.hidden = true;
    };
  }

  async function stepConfigure(body, B) {
    if (!B.pkgTouched) B.package_id = await api("suggest_package", B.app_name);
    const c = consoleOf(B.console);
    const bios = c.bios ? (await api("list_bios")).filter((b) => b.console === B.console && b.valid) : [];
    if (c.bios && !B.bios_id && bios.length) B.bios_id = (bios.find((b) => b.preferred) || bios[0]).id;
    body.innerHTML = `
      <div class="grid-2"><div class="panel"><div class="form">
        <div class="field full"><label for="appName">App name on the phone</label>
          <input class="input" id="appName" maxlength="50" value="${esc(B.app_name)}"><span class="hint">Shown under the icon, in Settings and in recent apps.</span></div>
        <div class="field full"><label for="pkg">Package ID</label>
          <input class="input mono" id="pkg" value="${esc(B.package_id)}" spellcheck="false"><span class="hint" id="pkgHint">Android's unique name for this app. Generated from the app name; change it only if you need to.</span></div>
        <div class="field full"><label>App icon</label><div class="icon-pick">
          <div class="app-icon" id="iconBox">${B.icon_url ? `<img src="${B.icon_url}" alt="">` : esc(initials(B.app_name))}</div>
          <div class="stack" style="gap:6px"><div class="row"><button class="btn" id="pickIcon">Choose image</button>${B.icon_url ? `<button class="btn ghost" id="clearIcon">Remove</button>` : ""}</div>
          <span class="hint">Square PNG or JPG, at least 512 × 512 pixels.</span></div></div></div>
        <div class="field full"><label>Game information</label><div id="infoBox" class="muted small">Looking up the game…</div></div>
        ${c.screen_layouts ? `<div class="field full"><label>Screen layout</label><div class="layouts" id="dsLayouts">
          ${c.screen_layouts.map((l) => `<button class="layout ${B.ds_layout === l.id ? "on" : ""}" data-l="${l.id}">${layoutPicture(l.id)}<h3>${esc(l.name)}</h3><p>${esc(l.desc)}</p></button>`).join("")}</div>
          <span class="hint">In the game: the ⛶ button (or L3 on a controller) switches between full screen and both screens; the ⇄ button (or R3) swaps the screens. Also in the game menu. TV builds start with Large top screen.</span></div>`
        : `<div class="field full"><label>Screen orientation</label><div class="seg" id="orient">
          ${[["landscape", "Landscape"], ["portrait", "Portrait"], ["auto", "Follow the phone"]].map(([k, t]) => `<button data-o="${k}" class="${B.orientation === k ? "on" : ""}">${t}</button>`).join("")}</div></div>`}
        ${c.controls ? `<div class="field full"><label>Wii controller</label><div class="layouts" id="wiiControls">
          ${c.controls.map((x) => `<button class="layout ${(B.wii_controls || "sideways") === x.id ? "on" : ""}" data-wc="${x.id}"><h3>${esc(x.name)}</h3><p>${esc(x.desc)}</p></button>`).join("")}</div>
          <span class="hint">Pick the one the game is played with. The touch controls and your Bluetooth controller follow this choice.</span></div>` : ""}
        ${c.bios ? `<div class="field full"><label for="biosSel">${esc(c.short)} BIOS to include</label>
          ${bios.length ? `<select class="select input" id="biosSel">${bios.map((b) => `<option value="${b.id}" ${b.id === B.bios_id ? "selected" : ""}>${esc(b.filename)} (v${esc(b.version || "?")}, ${esc(b.region || "region unknown")})</option>`).join("")}</select>`
            : `<span class="hint ${c.bios.required ? "bad" : ""}">${esc(c.bios.note)} <a class="link" id="toBios">Open BIOS Manager</a></span>`}</div>` : ""}
      </div>
      <div class="builder-foot"><span class="hint bad" id="formErr"></span><span class="spacer"></span><button class="btn primary" id="cont">Continue</button></div></div>
      <div class="panel" style="display:grid;justify-items:center;gap:14px"><span class="muted small">On the phone's home screen</span>
        <div class="phone-preview"><div class="app-icon" id="iconBox2">${B.icon_url ? `<img src="${B.icon_url}" alt="">` : esc(initials(B.app_name))}</div><div class="name" id="nameBox">${esc(B.app_name)}</div></div>
        <span class="faint small" style="text-align:center;max-width:260px">${c.buildable ? "Touch controls, Bluetooth controllers, saves and the pause menu are built into every " + esc(c.engine) + " app." : "Controls and engine settings for " + esc(c.engine) + " arrive with its template (" + esc(c.status) + ")."}</span></div></div>`;

    const name = $("#appName", body), pkg = $("#pkg", body);
    let pkgTimer;
    const checkPkg = async () => {
      const v = await api("validate_package", pkg.value.trim(), B.id);
      pkg.classList.toggle("bad", !v.ok);
      const h = $("#pkgHint", body);
      h.classList.toggle("bad", !v.ok);
      h.textContent = v.ok ? "Android's unique name for this app. Generated from the app name; change it only if you need to." : v.error;
      return v.ok;
    };
    name.oninput = async () => {
      B.app_name = name.value;
      $("#nameBox", body).textContent = B.app_name;
      if (!B.icon_url) { $("#iconBox", body).textContent = initials(B.app_name); $("#iconBox2", body).textContent = initials(B.app_name); }
      if (!B.pkgTouched) { clearTimeout(pkgTimer); pkgTimer = setTimeout(async () => { B.package_id = pkg.value = await api("suggest_package", B.app_name); checkPkg(); }, 200); }
    };
    pkg.oninput = () => { B.pkgTouched = true; B.package_id = pkg.value.trim(); clearTimeout(pkgTimer); pkgTimer = setTimeout(checkPkg, 200); };
    $$("#wiiControls .layout", body).forEach((b) => b.onclick = () => { B.wii_controls = b.dataset.wc; $$("#wiiControls .layout", body).forEach((x) => x.classList.toggle("on", x === b)); });
    $$("#dsLayouts .layout", body).forEach((b) => b.onclick = () => { B.ds_layout = b.dataset.l; $$("#dsLayouts .layout", body).forEach((x) => x.classList.toggle("on", x === b)); });
    $$("#orient button", body).forEach((b) => b.onclick = () => { B.orientation = b.dataset.o; $$("#orient button", body).forEach((x) => x.classList.toggle("on", x === b)); });
    $("#pickIcon", body).onclick = async () => {
      const r = await api("pick_icon");
      if (!r) return;
      if (!r.ok) return toast(r.error, "fail");
      B.icon_path = r.path; B.icon_url = r.data_url; go("builder");
    };
    const ci = $("#clearIcon", body); if (ci) ci.onclick = () => { B.icon_path = null; B.icon_url = null; go("builder"); };
    name.addEventListener("input", () => { B.nameTouched = true; });
    builderInfo(body, B);
    const bs = $("#biosSel", body); if (bs) bs.onchange = () => { B.bios_id = +bs.value; };
    const tb = $("#toBios", body); if (tb) tb.onclick = () => { S.biosConsole = B.console; go("bios"); };
    $("#cont", body).onclick = async () => {
      if (!B.app_name.trim()) { $("#formErr", body).textContent = "Enter the app name."; name.focus(); return; }
      if (!(await checkPkg())) { pkg.focus(); return; }
      if (c.bios && c.bios.required && !B.bios_id) { $("#formErr", body).textContent = "Import a BIOS first."; return; }
      next(B);
    };
  }

  // Small diagram of where the DS screens and the controls go on the phone.
  function layoutPicture(id) {
    const scr = (x, y, w, h, touch) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="1.5" class="${touch ? "scr touch" : "scr"}"/>`;
    const pad = (cx, cy) => `<circle cx="${cx}" cy="${cy}" r="7" class="pad"/>`;
    if (id === "rotate") return `<svg viewBox="0 0 120 80" class="phone-pic"><rect x="6" y="10" width="30" height="60" rx="5" class="body"/>${scr(9, 14, 24, 18)}${scr(9, 33, 24, 18, 1)}${pad(14, 61)}${pad(28, 61)}<path d="M41 40 h12 m-4 -4 l4 4 l-4 4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" opacity=".55"/><rect x="58" y="22" width="60" height="36" rx="5" class="body"/>${scr(70, 26, 28, 21)}${scr(99, 37, 11, 8, 1)}${pad(63, 50)}${pad(113, 50)}</svg>`;
    if (id === "stacked") return `<svg viewBox="0 0 120 80" class="phone-pic"><rect x="40" y="2" width="40" height="76" rx="6" class="body"/>${scr(44, 7, 32, 24)}${scr(44, 32, 32, 24, 1)}${pad(50, 68)}${pad(70, 68)}</svg>`;
    if (id === "side") return `<svg viewBox="0 0 120 80" class="phone-pic"><rect x="2" y="18" width="116" height="48" rx="6" class="body"/>${scr(25, 24, 35, 26)}${scr(60, 24, 35, 26, 1)}${pad(13, 55)}${pad(107, 55)}</svg>`;
    return `<svg viewBox="0 0 120 80" class="phone-pic"><rect x="2" y="18" width="116" height="48" rx="6" class="body"/>${scr(24, 22, 52, 40)}${scr(77, 42, 20, 15, 1)}${pad(13, 55)}${pad(107, 55)}</svg>`;
  }

  function stepProfile(body, B) {
    const c = consoleOf(B.console);
    body.innerHTML = `
      <div class="panel"><h2>Performance profile</h2>
        <div class="profiles">${S.profiles.map((p) => `<button class="profile ${B.profile === p.id ? "on" : ""}" data-p="${p.id}"><h3>${esc(p.name)}</h3><p>${esc(p.desc)}</p></button>`).join("")}</div>
        ${(c.resolutions || []).length ? `<div id="resWrap" ${B.profile === "quality" ? "" : "hidden"}>
        <h2 style="margin-top:24px">Resolution</h2>
        <div class="seg" id="res">${c.resolutions.map((r) => `<button data-r="${r.id}" class="${(B.resolution || "auto") === r.id ? "on" : ""}" title="${esc(r.detail)}">${esc(r.label)}</button>`).join("")}</div>
        <p class="muted small" id="resNote" style="margin-top:8px"></p></div>` : ""}
        ${(c.graphics || []).length ? `<h2 style="margin-top:24px">Graphics</h2>
        <div class="seg" id="gfx">${c.graphics.map((x) => `<button data-g="${x.id}" class="${(B.graphics || "vulkan") === x.id ? "on" : ""}">${esc(x.name)}</button>`).join("")}</div>
        <p class="muted small" id="gfxNote" style="margin-top:8px">${esc((c.graphics.find((x) => x.id === (B.graphics || "vulkan")) || c.graphics[0]).desc)} You can still switch in the game's pause menu (Graphics).</p>` : ""}
        <h2 style="margin-top:24px">Screen shape</h2>
        ${(c.features || []).includes("widescreen") ? `
        <label class="toggle-row" for="wide">
          <span class="grow"><h3>Widescreen hack</h3><p>Shows more of the game world to the sides so it fills a 16:9 view instead of 4:3. Works well in many games; in some, menus stretch or objects pop in at the edges. Try it, and rebuild without it if a game looks wrong.${c.widescreen_how ? "<br><span class='faint'>" + esc(c.widescreen_how) + "</span>" : ""}</p></span>
          <input type="checkbox" id="wide" class="switch" ${B.widescreen ? "checked" : ""}>
        </label>` : `<p class="muted">${esc(c.widescreen_note || "Widescreen isn't available for this console.")}</p>`}
        ${(c.features || []).includes("tv") ? `
        <h2 style="margin-top:24px">Where you'll play</h2>
        <label class="toggle-row" for="tvSwitch">
          <span class="grow"><h3>TV support</h3><p>Also runs on Android TV, Google TV and Fire TV (Fire OS) devices, which mostly need 32-bit engines, and shows the game on the TV home screen with its own banner. Always opens sideways. The same APK still works on phones; it's about ${B.console === "psp" ? "8" : "2"} MB bigger. Play on TV with a Bluetooth controller.${B.console === "nds" ? "<br><span class='faint'>DS on TV: large top screen on the left, touch screen on the right. Left stick = D-pad, right stick moves the touch pencil, R2 touches, R3 swaps the screens. On 32-bit TV devices the app uses the faster DeSmuME engine automatically.</span>" : ""}${B.console === "n64" ? "<br><span class='faint'>N64 on TV uses lighter graphics settings for weak TV chips (a few special effects may be missing). On slow devices pick Performance or Battery Saver.</span>" : ""}</p></span>
          <input type="checkbox" id="tvSwitch" class="switch" ${B.tv ? "checked" : ""}>
        </label>` : ""}
        ${B.console === "nds" ? `<p class="muted small" style="margin-top:12px">DS games always run at their original resolution (this DS engine can't render sharper), so the profile mainly changes battery use.</p>` : ""}
        ${B.console === "ps2" ? `<p class="muted small" style="margin-top:12px">For PS2, the engine (ARMSX2) picks graphics settings for each phone automatically. The game app looks like your other Tirso games: same touch controls and pause menu. Best on Galaxy S24 / S25.</p>` : ""}
        <p class="muted small" style="margin-top:14px">${c.buildable ? "Your profile and screen choices become real " + esc(c.engine) + " settings inside the APK." : "These become real " + esc(c.engine) + " settings when its engine arrives. Your choices are saved with the game now."}</p>
        <div class="builder-foot">
          <button class="btn" id="save">${B.id ? "Save changes" : "Save game app"}</button><span class="spacer"></span>
          <span class="hint">${c.buildable ? (c.tested ? "" : "First builds are the phone test for this engine.") : esc(c.engine) + " template isn't built yet (" + esc(c.status.replace(/^.*\((.*)\).*$/, "$1")) + ")."}</span>
          <button class="btn primary" id="gen" ${c.buildable && B.report && B.report.can_build ? "" : "disabled"}>Generate APK</button>
        </div></div>`;
    $$(".profile", body).forEach((b) => b.onclick = () => {
      B.profile = b.dataset.p; $$(".profile", body).forEach((x) => x.classList.toggle("on", x === b));
      const rw = $("#resWrap", body); if (rw) rw.hidden = B.profile !== "quality";   // resolution choice belongs to High Quality
    });
    const wide = $("#wide", body); if (wide) wide.onchange = () => { B.widescreen = wide.checked; };
    $$("#gfx button", body).forEach((b) => b.onclick = () => {
      B.graphics = b.dataset.g; $$("#gfx button", body).forEach((x) => x.classList.toggle("on", x === b));
      $("#gfxNote", body).textContent = c.graphics.find((x) => x.id === B.graphics).desc + " You can still switch in the game's pause menu (Graphics).";
    });
    const resNote = () => {
      const n = $("#resNote", body); if (!n) return;
      const r = (c.resolutions || []).find((x) => x.id === (B.resolution || "auto")) || {};
      const heavy = { "1080p": 1, "1440p": 2 }[B.resolution] || 0;
      n.textContent = (r.detail || "") + (heavy && B.console === "ps2" ? ". PS2 at high resolutions needs a fast phone (Galaxy S24/S25); if a game slows down, rebuild at 720p or Auto." : "")
        + (heavy === 2 && B.console !== "ps2" ? ". Sharpest picture, uses more battery." : "")
        + (B.tv && B.resolution !== "auto" ? " On TV devices, Auto is safer: TV chips are slow." : "");
    };
    $$("#res button", body).forEach((b) => b.onclick = () => { B.resolution = b.dataset.r; $$("#res button", body).forEach((x) => x.classList.toggle("on", x === b)); resNote(); });
    resNote();
    const tvs = $("#tvSwitch", body); if (tvs) tvs.onchange = () => { B.tv = tvs.checked; resNote(); };
    const saveIt = async () => {
      const r = await api("save_game", { id: B.id, rom_id: B.rom_id, console: B.console, app_name: B.app_name.trim(), package_id: B.package_id, orientation: B.orientation, profile: B.profile, icon_path: B.icon_path, bios_id: B.bios_id, widescreen: B.widescreen, resolution: B.profile === "quality" ? B.resolution : "auto", ds_layout: B.ds_layout, wii_controls: B.wii_controls, graphics: B.graphics, tv: B.tv, info_dir: B.info_dir });
      if (!r.ok) { toast(r.error, "fail"); return null; }
      B.id = r.id;
      return r.id;
    };
    $("#save", body).onclick = async () => {
      const wasNew = !B.id;
      if (!(await saveIt())) return;
      toast(wasNew ? `“${B.app_name}” saved to My Games.` : "Changes saved.");
      S.builder = null; go("games");
    };
    $("#gen", body).onclick = async () => {
      const id = await saveIt();
      if (!id) return;
      S.builder = null;
      startBuild(id);
    };
  }

  async function startBuild(gameId) {
    const r = await api("generate_apk", gameId);
    if (!r.ok) return toast(r.error, "fail");
    if (r.queued) toast(`Added to the queue: it starts after ${plural(r.ahead, "build")} ahead of it.`);
    else S.watchBuild = r.id;
    go("builds");
  }

  // ================================================================ MY GAMES
  pages.games = async (el) => {
    const games = await api("list_games");
    el.innerHTML = `
      <div class="page-head"><div><h1>My Games</h1><p class="lede">Game apps you've set up. Each becomes its own APK with its own name, icon and saves.</p></div>
        <button class="btn primary" id="newGame">New game app</button></div>
      ${games.length ? `<div class="games">${games.map((g) => `
        <article class="game">
          <div class="game-top"><div class="app-icon">${g.icon ? `<img src="${g.icon}" alt="">` : esc(initials(g.app_name))}</div>
            <div class="grow" style="min-width:0"><h3 class="ellipsis">${esc(g.app_name)}</h3><div class="row" style="gap:8px;margin-top:4px"><span class="badge" style="--c:${g.color}">${esc(g.console_short || "?")}</span><span class="muted small">${esc(g.profile_name)}${(g.settings || {}).tv ? ", TV + phone" : ""}${(g.settings || {}).widescreen ? ", widescreen" : ""}${(g.settings || {}).resolution && g.settings.resolution !== "auto" ? ", " + esc(g.settings.resolution) : ""}${(g.settings || {}).wii_controls ? ", " + esc(({ sideways: "Wii Remote sideways", nunchuk: "Wii Remote + Nunchuk", classic: "Classic Controller", gamecube: "GameCube controller" })[g.settings.wii_controls] || "") : ""}${(g.settings || {}).ds_layout ? ", " + esc(({ rotate: "turns with the phone", stacked: "stacked screens", side: "side by side", large: "large top screen" })[g.settings.ds_layout] || "") : ""}</span></div></div></div>
          <dl class="kv"><dt>Package</dt><dd class="mono">${esc(g.package_id)}</dd><dt>Game file</dt><dd title="${esc(g.rom_path)}">${esc(g.rom_name)}</dd><dt>Screen</dt><dd>${esc({ landscape: "Landscape", portrait: "Portrait (upright)", auto: "Follows the phone" }[g.orientation])}</dd></dl>
          <div>${g.rom_status === "missing" ? `<span class="chip fail"><span class="dot"></span>Game file missing</span>` : g.buildable ? `<span class="chip ok"><span class="dot"></span>Ready to generate</span>${g.last_apk ? `<div class="faint small" style="margin-top:6px">Last APK: ${esc(when(g.last_build_at))}</div>` : ""}` : `<span class="chip warn"><span class="dot"></span>Waiting for ${esc(g.engine)}</span>`}
</div><div class="row" style="border-top:1px solid var(--line);padding-top:12px"><span class="spacer"></span><button class="btn ghost small danger" data-del="${g.id}">Remove</button><button class="btn small" data-edit="${g.id}">Edit</button>${g.buildable && g.rom_status !== "missing" ? `<button class="btn small primary" data-gen="${g.id}">Generate APK</button>` : ""}</div>
        </article>`).join("")}</div>`
        : `<div class="empty"><h3>No game apps yet</h3>Use Game Builder to turn a game from your library into an app.</div>`}`;
    $("#newGame", el).onclick = () => go("builder", { fresh: true });
    $$("[data-gen]", el).forEach((b) => b.onclick = () => startBuild(+b.dataset.gen));
    $$("[data-edit]", el).forEach((b) => b.onclick = () => go("builder", { edit: games.find((g) => g.id === +b.dataset.edit) }));
    $$("[data-del]", el).forEach((b) => b.onclick = async () => {
      if (!(await confirmBox("Remove this game app?", "Only the setup is removed. The game file, BIOS and any APKs already built stay on your PC.", "Remove"))) return;
      await api("delete_game", +b.dataset.del); toast("Game app removed."); go("games");
    });
  };

  // ================================================================ ENGINE MANAGER
  const chipFor = (ok, badText, okText) => ok ? `<span class="chip ok"><span class="dot"></span>${okText}</span>` : `<span class="chip ${badText === "Missing" ? "fail" : ""}">${badText}</span>`;

  async function backupKey() {
    const r = await api("backup_signing_key");
    if (!r) return;
    toast(r.ok ? "Key backed up to " + r.path : r.error, r.ok ? "ok" : "fail");
  }

  pages.engines = async (el) => {
    const engines = await api("list_engines");
    const t = await api("tools_status");
    el.innerHTML = `
      <div class="page-head"><div><h1>Engine Manager</h1>
        <p class="lede">Each engine becomes a tested Android template that Tirso fills in with your game. Each one has been tested on your phones.</p></div></div>
      <div class="panel" style="padding:8px 10px"><div class="table-wrap"><table>
        <thead><tr><th>Engine</th><th>Consoles</th><th>License</th><th>Installed</th><th>Status</th><th></th></tr></thead>
        <tbody>${engines.map((e) => `<tr>
          <td><b>${esc(e.engine)}</b></td>
          <td><div class="row" style="gap:6px">${e.consoles.map((s) => badge(S.consoles.find((c) => c.short === s))).join("")}</div></td>
          <td class="muted">${esc(e.license)}</td>
          <td class="muted">${e.installed_version ? esc(e.installed_version) : "Not yet"}</td>
          <td><span class="chip ${e.ready && e.tested ? "ok" : e.ready ? "warn" : ""}">${e.ready ? `<span class="dot"></span>` : ""}${esc(e.status)}</span></td>
          <td style="text-align:right">${e.source ? `<a class="link small" data-url="${esc(e.source)}">Official source</a>` : ""}</td></tr>`).join("")}</tbody></table></div></div>
      <div class="panel" style="margin-top:18px"><h2>Build tools on this PC</h2><div class="list">
        <div class="list-row"><div class="grow"><h3>Java runtime</h3><div class="muted small">Runs the packaging tools. Included with Tirso; unpacked automatically on your first build.</div></div>${chipFor(t.java_ready, t.java ? "Included" : "Missing", "Ready")}</div>
        <div class="list-row"><div class="grow"><h3>Apktool</h3><div class="muted small">Opens the engine template and puts your game, name and icon into it.</div></div>${chipFor(t.apktool, "Missing", "Ready")}</div>
        <div class="list-row"><div class="grow"><h3>APK signer</h3><div class="muted small">Aligns, signs and verifies every APK.</div></div>${chipFor(t.signer, "Missing", "Ready")}</div>
        <div class="list-row"><div class="grow"><h3>Your signing key</h3><div class="muted small">${t.key ? "Created " + esc(t.key_created) + ". Phones only accept updates to a game app signed with the same key, so keep a backup." : "Created automatically with your first APK."}</div></div>
          ${t.key ? `<button class="btn small" id="backupKey">Back up key</button>` : `<span class="chip">Not yet</span>`}</div>
      </div></div>
      <div class="notice" style="margin-top:18px"><div>Engines show <b>Ready</b> only after a game built with them has been tested on a Galaxy S25, S24 and S20 FE. PlayStation 1 and PlayStation 3 are planned for a later version.</div></div>`;
    $$("[data-url]", el).forEach((a) => a.onclick = () => api("open_url", a.dataset.url));
    const bk = $("#backupKey", el); if (bk) bk.onclick = backupKey;
  };

  // ================================================================ BIOS MANAGER
  async function importBios(consoleId, paths) {
    const r = paths ? await api("import_bios", consoleId, paths) : await api("pick_bios_files", consoleId);
    (r.results || []).forEach((x) => toast(`${x.file}: ${x.message}`, x.ok ? "ok" : "fail"));
    go("bios");
  }

  async function importDriver(paths, slot) {
    slot = slot || S.driverSlot || "ps2";
    const r = paths ? await api("import_gpu_driver", slot, paths) : await api("pick_gpu_driver", slot);
    if (r.message) toast(r.message, r.ok ? "ok" : "fail");
    go("bios");
  }

  pages.bios = async (el) => {
    S.biosConsole = S.biosConsole || "ps2";
    const all = await api("list_bios");
    const drivers = { ps2: await api("get_gpu_driver", "ps2"), dolphin: await api("get_gpu_driver", "dolphin") };
    const driverPanel = (slot, cid, title, intro) => {
      const drv = drivers[slot];
      return `<div class="panel spine-panel">
          <div class="row" style="margin-bottom:12px">${badge(consoleOf(cid))}<h2 style="margin:0">${esc(title)}</h2><span class="spacer"></span>
            <button class="btn" data-drv-import="${slot}">${drv ? "Replace driver" : "Add GPU driver"}</button></div>
          <p class="muted" style="margin:0 0 14px">${esc(intro)}</p>
          ${drv ? `<div class="table-wrap"><table><thead><tr><th>Driver</th><th>Version</th><th>Size</th><th></th></tr></thead><tbody>
            <tr><td><b>${esc(drv.name)}</b><div class="faint small">${esc(drv.filename)}${drv.exists ? "" : " (file missing: add it again)"}</div></td>
            <td>${esc(drv.version || "?")}</td><td class="muted">${esc(drv.size_h)}</td>
            <td style="text-align:right"><button class="btn ghost small danger" data-drv-rm="${slot}">Remove</button></td></tr></tbody></table></div>`
            : `<div class="empty">No GPU driver. These apps use the phone's own driver.</div>`}
        </div>`;
    };
    const section = (cid, intro) => {
      const c = consoleOf(cid);
      const rows = all.filter((b) => b.console === cid);
      return `<div class="panel spine-panel">
        <div class="row" style="margin-bottom:12px">${badge(c)}<h2 style="margin:0">${esc(c.name)}</h2><span class="spacer"></span>
          <button class="btn ${cid === "ps2" ? "primary" : ""}" data-import="${cid}">Import ${esc(c.short)} BIOS</button></div>
        <p class="muted" style="margin:0 0 14px">${esc(intro)}</p>
        ${rows.length ? `<div class="table-wrap"><table><thead><tr><th>File</th><th>Version</th><th>Region</th><th>SHA-256</th><th>Default</th><th></th></tr></thead><tbody>
          ${rows.map((b) => `<tr><td><b>${esc(b.filename)}</b><div class="faint small">${esc(b.message)}</div></td>
            <td>${esc(b.version || "?")}</td><td class="muted">${esc(b.region || "Unknown")}</td>
            <td class="mono faint" title="${esc(b.sha256)}">${esc(b.sha256.slice(0, 16))}…</td>
            <td>${b.preferred ? `<span class="chip ok"><span class="dot"></span>Default</span>` : `<button class="btn ghost small" data-pref="${b.id}">Make default</button>`}</td>
            <td style="text-align:right"><button class="btn ghost small danger" data-rm="${b.id}">Remove</button></td></tr>`).join("")}
          </tbody></table></div>`
          : `<div class="empty">${cid === "ps2" ? "No PS2 BIOS yet. You'll need one before any PS2 game can be built." : "None imported. DS games still work without them."}</div>`}
      </div>`;
    };
    el.innerHTML = `
      <div class="page-head"><div><h1>BIOS Manager</h1>
        <p class="lede">BIOS files dumped from consoles you own. Tirso checks each one, keeps a private copy on this PC and never downloads or shares them.</p></div>
        <button class="btn ghost" id="openBios">Show BIOS folder</button></div>
      <div class="stack">
        ${section("ps2", "Required for every PS2 game. The default BIOS is placed inside each PS2 app so it starts with no setup on the phone.")}
        ${section("nds", "Optional. Import bios7.bin, bios9.bin and firmware.bin for better compatibility with some DS games.")}
        ${driverPanel("ps2", "ps2", "PS2 GPU driver", "Optional. A custom Adreno driver pack (.zip), like Mesa Turnip, that you already have. Every PS2 app built afterwards includes it and switches to Vulkan with it on phones with a Snapdragon (Adreno) GPU. Other phones keep their own driver.")}
        ${driverPanel("dolphin", "gc", "GameCube & Wii GPU driver", "Optional. A custom Adreno driver pack (.zip) for Dolphin, like Mesa Turnip. Every GameCube and Wii app built afterwards includes it and uses it with Vulkan on Snapdragon (Adreno) phones; other phones, or a driver that doesn't start, fall back to the phone's own driver. The in-game Graphics menu can switch it off.")}
      </div>`;
    $$("[data-drv-import]", el).forEach((b) => b.onclick = () => { S.driverSlot = b.dataset.drvImport; importDriver(null, b.dataset.drvImport); });
    $$("[data-drv-rm]", el).forEach((b) => b.onclick = async () => {
      if (!(await confirmBox("Remove this GPU driver?", "New builds will use the phone's own driver. Apps you already built keep theirs.", "Remove"))) return;
      await api("remove_gpu_driver", b.dataset.drvRm); toast("GPU driver removed."); go("bios");
    });
    $$("[data-import]", el).forEach((b) => b.onclick = () => { S.biosConsole = b.dataset.import; importBios(b.dataset.import); });
    $$("[data-pref]", el).forEach((b) => b.onclick = async () => { await api("set_preferred_bios", +b.dataset.pref); go("bios"); });
    $$("[data-rm]", el).forEach((b) => b.onclick = async () => {
      if (!(await confirmBox("Remove this BIOS?", "Tirso's private copy is deleted. Your original file is not touched.", "Remove"))) return;
      await api("remove_bios", +b.dataset.rm); toast("BIOS removed."); go("bios");
    });
    $("#openBios", el).onclick = () => api("open_folder", "bios");
  };

  // ================================================================ BUILD CENTER
  const STAGE_LABELS = [["check", "Checking the game and tools"], ["game", "Preparing the game file"], ["tools", "Preparing build tools"], ["key", "Signing key"], ["unpack", "Opening the engine template"], ["customise", "Adding the game, name, icon and settings"], ["pack", "Building the APK"], ["sign", "Signing and verifying"], ["save", "Saving to your output folder"]];
  const STATUS_CHIP = { success: ["ok", "Built"], failed: ["fail", "Failed"], cancelled: ["", "Cancelled"], running: ["warn", "Building"], queued: ["", "Waiting"] };

  function installSteps() {
    return `<ol class="install-steps">
      <li>Copy the APK to your phone: plug it in with a USB cable, or use <b>Quick Share</b> from Windows.</li>
      <li>On the phone, open <b>My Files</b>, find the APK (usually in Downloads) and tap it.</li>
      <li>If Android asks, allow <b>My Files</b> to install unknown apps.</li>
      <li>If Google Play Protect warns about an unknown developer, tap <b>More details</b>, then <b>Install anyway</b>. That's expected: the app is signed with your own key, not a Play Store one.</li>
    </ol>`;
  }

  pages.builds = async (el) => {
    const builds = await api("list_builds");
    const cur = await api("current_build");
    const watchId = (cur && cur.id) || S.watchBuild || ((builds.find((b) => b.status !== "queued") || {}).id) || null;
    const watch = watchId ? await api("get_build", watchId) : null;
    const name = watch ? (builds.find((b) => b.id === watch.id) || {}).app_name : "";
    el.innerHTML = `
      <div class="page-head"><div><h1>Build Center</h1><p class="lede">Progress, logs and results for every APK you generate. Builds run one at a time; the rest wait in the queue.</p></div>
        <button class="btn" id="devicesBtn">Phones and TVs</button></div>
      ${watch ? `<div class="panel build-live" id="live">${liveBuild(watch, name)}</div>` : ""}
      ${builds.length ? `<div class="panel" style="padding:8px 10px;margin-top:18px"><div class="table-wrap"><table>
        <thead><tr><th>Game app</th><th>Started</th><th>Time</th><th>APK</th><th>Status</th><th></th></tr></thead><tbody>
        ${builds.map((b) => { const [k, t] = STATUS_CHIP[b.status] || ["", b.status]; return `<tr>
          <td><div class="row" style="gap:10px">${badge(consoleOf(b.console))}<b>${esc(b.app_name || "Removed game app")}</b></div></td>
          <td class="muted">${esc(when(b.started_at))}</td>
          <td class="muted">${b.seconds != null ? b.seconds + " s" : "…"}</td>
          <td class="muted">${b.apk_size_h ? esc(b.apk_size_h) : ""}</td>
          <td><span class="chip ${k}" title="${esc(b.message || "")}"><span class="dot"></span>${t}</span></td>
          <td style="text-align:right;white-space:nowrap">
            ${b.apk_exists ? `<button class="btn ghost small" data-install="${b.id}">Install</button><button class="btn ghost small" data-show="${esc(b.apk_path)}">Show APK</button>` : ""}
            ${b.status === "queued" ? "" : `<button class="btn ghost small" data-log="${b.id}">Log</button>`}
            ${b.status === "queued" ? `<button class="btn ghost small" data-unqueue="${b.id}">Remove from queue</button>` : ""}
            ${b.status !== "running" && b.status !== "queued" && b.game_id ? `<button class="btn ghost small" data-rebuild="${b.game_id}">Build again</button>` : ""}
          </td></tr>`; }).join("")}
        </tbody></table></div></div>`
        : `<div class="empty"><h3>No builds yet</h3>Open My Games, or finish a game in Game Builder, and choose Generate APK.</div>`}`;
    bindBuildPage(el);
  };

  function liveBuild(b, name) {
    const idx = STAGE_LABELS.findIndex(([k]) => k === b.stage);
    const running = b.status === "running";
    const pct = b.status === "success" ? 100 : Math.round((Math.max(idx, 0) / STAGE_LABELS.length) * 100);
    let result = "";
    if (b.status === "success") {
      result = `<div class="result ok"><h3>APK ready: ${esc((b.apk_path || "").split(/[\\/]/).pop())}</h3>
        <p class="muted small">${esc(b.apk_path || "")}</p>
        ${b.data_path ? `<p class="small" style="margin:0 0 8px">This game also has a <b>game data file</b> (too big for the APK). The Install button sends both. To copy by hand, put the folder <span class="mono">${esc(b.data_path.split(/[\\/]/).slice(-2, -1)[0])}</span> from <span class="mono">${esc(b.data_path.split(/[\\/]/).slice(-3, -2)[0])}</span> into <b>Android/obb</b> on the phone.</p>` : ""}
        <div class="row" style="margin:10px 0 4px"><button class="btn primary" data-install="${b.id}">Install on a phone or TV</button><button class="btn" data-show="${esc(b.apk_path || "")}">Show in folder</button></div>
        <details style="margin-top:10px"><summary class="muted small">Or copy it by hand</summary>${installSteps()}</details></div>`;
    } else if (b.status === "failed") {
      result = `<div class="result fail"><h3>The build didn't finish</h3><p>${esc(b.message || "See the log below.")}</p>
        <div class="row"><button class="btn" data-rebuild="${b.game_id}">Try again</button></div></div>`;
    } else if (b.status === "cancelled") {
      result = `<div class="result"><h3>Build cancelled</h3><div class="row"><button class="btn" data-rebuild="${b.game_id}">Build again</button></div></div>`;
    }
    return `<div class="row" style="margin-bottom:14px"><h2 style="margin:0">${running ? "Building" : "Last build"}: ${esc(name || "")}</h2><span class="spacer"></span>
        ${running ? `<button class="btn" id="cancelBuild" data-id="${b.id}">Cancel</button>` : ""}</div>
      <div class="bar"><i id="buildBar" style="width:${pct}%"></i></div>
      <div class="build-grid">
        <ol class="stages" id="stages">${STAGE_LABELS.map(([k, t], i) => {
          const state = b.status === "success" || i < idx ? "done" : i === idx ? (running ? "now" : b.status === "failed" ? "bad" : "") : "";
          return `<li class="${state}" data-stage="${k}">${esc(t)}</li>`; }).join("")}</ol>
        <div>${result}<details ${running || b.status === "failed" ? "open" : ""}><summary class="muted small">Technical log</summary><pre class="log" id="buildLog">${esc(b.log || "")}</pre></details></div>
      </div>`;
  }

  // ---------------------------------------------------------------- devices / install
  const devicesHelp = `<details class="help"><summary>How to connect a phone or TV</summary>
    <div class="help-body">
    <h3>Galaxy phone (USB cable)</h3>
    <ol><li>Settings > About phone > Software information: tap <b>Build number</b> 7 times.</li>
    <li>Settings > Developer options: turn on <b>USB debugging</b>.</li>
    <li>Plug the phone into this PC and tap <b>Allow</b> on the phone.</li>
    <li>If the phone still doesn't appear, install Samsung's "USB Driver for Mobile Phones" from samsung.com/developers, then reconnect.</li></ol>
    <h3>Google TV Streamer or TCL Google/Android TV (Wi-Fi)</h3>
    <ol><li>Settings > System > About: select <b>Android TV OS build</b> 7 times.</li>
    <li>Settings > System > Developer options: turn on <b>USB debugging</b> and <b>Wireless debugging</b>.</li>
    <li>Open Wireless debugging > <b>Pair device with pairing code</b>, and type the address and code below under "Pair".</li>
    <li>Then type the address shown on the Wireless debugging screen under "Connect".</li></ol>
    <h3>Fire TV Stick 4K Plus (Wi-Fi)</h3>
    <ol><li>Settings > My Fire TV > About: select the device name 7 times.</li>
    <li>Settings > My Fire TV > Developer options: turn on <b>ADB debugging</b>.</li>
    <li>Find its IP address in My Fire TV > About > Network, type it under "Connect", and accept the prompt on the TV.</li></ol>
    </div></details>`;

  async function openDevices(buildId) {
    const m = $("#modal");
    m.hidden = false;
    m.onclick = (e) => { if (e.target === m || e.target.dataset.a === "close") { m.hidden = true; S.installing = null; } };
    const draw = async () => {
      const st = await api("list_devices");
      let body;
      if (!st.installed) {
        body = `<p>To install games straight from this PC, Tirso uses Google's ADB tool (about 8 MB, downloaded from Google once and checked against Google's checksum).</p>
          <div class="row"><button class="btn primary" id="setupAdb">Set up device installs</button></div>
          <div class="bar" style="margin-top:14px;display:none" id="adbBar"><i style="width:0%"></i></div>`;
      } else {
        const devs = st.devices;
        body = `${st.error ? `<p class="hint bad">${esc(st.error)}</p>` : ""}
          <div class="list">${devs.length ? devs.map((d) => `
            <div class="list-row"><div class="grow" style="min-width:0">
              <h3 class="ellipsis">${esc(d.name || d.serial)}</h3>
              <div class="row" style="gap:6px;margin-top:4px">
                ${d.state === "device" ? `<span class="chip">${d.is_tv ? "TV" : "Phone"}</span>
                <span class="chip ${d.has64 ? "" : "warn"}">${d.has64 ? "64-bit" : "32-bit only"}</span>
                <span class="chip">Android ${esc(d.android)}</span>${d.free_h ? `<span class="chip">${esc(d.free_h)} free</span>` : ""}`
                : `<span class="chip warn"><span class="dot"></span>${esc(d.state_text)}</span>`}
                ${d.wifi ? `<span class="chip">Wi-Fi</span>` : `<span class="chip">USB</span>`}
              </div></div>
              ${d.wifi ? `<button class="btn ghost small" data-disc="${esc(d.serial)}">Disconnect</button>` : ""}
              ${buildId && d.state === "device" ? `<button class="btn small primary" data-inst="${esc(d.serial)}">Install</button>` : ""}
            </div>`).join("") : `<div class="empty" style="padding:16px">No phones or TVs found. Connect one using the steps below, then press Refresh.</div>`}</div>
          <div class="install-log" id="installLog" ${S.installing ? "" : "hidden"}></div>
          <div class="wifi-forms">
            <div class="field"><label for="connAddr">Connect over Wi-Fi</label><div class="row">
              <input class="input" id="connAddr" placeholder="192.168.1.50 or 192.168.1.50:37123"><button class="btn" id="connBtn">Connect</button></div></div>
            <div class="field"><label for="pairAddr">Pair (Google TV / Android 11+)</label><div class="row">
              <input class="input" id="pairAddr" placeholder="Pairing address, e.g. 192.168.1.50:41235">
              <input class="input" id="pairCode" placeholder="6-digit code" style="max-width:130px"><button class="btn" id="pairBtn">Pair</button></div></div>
          </div>${devicesHelp}`;
      }
      m.innerHTML = `<div class="modal wide" role="dialog" aria-modal="true">
        <div class="row" style="margin-bottom:6px"><h2 style="margin:0">${buildId ? "Install on a device" : "Phones and TVs"}</h2><span class="spacer"></span>
        ${st.installed ? `<button class="btn ghost small" id="refreshDev">Refresh</button>` : ""}<button class="btn ghost small" data-a="close">Close</button></div>
        ${body}</div>`;
      const q = (id) => m.querySelector(id);
      if (q("#setupAdb")) q("#setupAdb").onclick = async () => {
        q("#setupAdb").disabled = true; q("#setupAdb").textContent = "Downloading from Google…"; q("#adbBar").style.display = "block";
        const r = await api("setup_adb");
        if (!r.ok) { toast(r.error, "fail"); q("#setupAdb").disabled = false; q("#setupAdb").textContent = "Try again"; return; }
        toast("Device installs are set up."); draw();
      };
      if (q("#refreshDev")) q("#refreshDev").onclick = draw;
      m.querySelectorAll("[data-disc]").forEach((b) => b.onclick = async () => { await api("disconnect_device", b.dataset.disc); draw(); });
      m.querySelectorAll("[data-inst]").forEach((b) => b.onclick = async () => {
        const r = await api("install_build", buildId, b.dataset.inst, true);
        if (!r.ok) return toast(r.error, "fail");
        S.installing = b.dataset.inst;
        m.querySelectorAll("[data-inst]").forEach((x) => (x.disabled = true));
        const log = q("#installLog"); log.hidden = false; log.textContent = "Installing on " + r.device + "…";
      });
      if (q("#connBtn")) q("#connBtn").onclick = async () => {
        const r = await api("connect_device", q("#connAddr").value);
        toast(r.ok ? r.message : r.error, r.ok ? "ok" : "fail"); if (r.ok) draw();
      };
      if (q("#pairBtn")) q("#pairBtn").onclick = async () => {
        const r = await api("pair_device", q("#pairAddr").value, q("#pairCode").value);
        toast(r.ok ? r.message : r.error, r.ok ? "ok" : "fail");
      };
    };
    m.innerHTML = `<div class="modal wide"><p class="muted">Looking for phones and TVs…</p></div>`;
    S.devicesDraw = draw;
    draw();
  }

  function onInstallEvent(event, data) {
    if (event === "adb_progress") {
      const bar = document.querySelector("#adbBar i"); if (bar) bar.style.width = data.pct + "%";
      return;
    }
    const log = document.querySelector("#installLog");
    if (event === "install_log" && log && data.line && !data.line.startsWith("$ ")) {
      log.textContent += "\n" + data.line;
    }
    if (event === "install_done") {
      S.installing = null;
      document.querySelectorAll("[data-inst]").forEach((x) => (x.disabled = false));
      if (data.ok) {
        const msg = `${data.app_name} installed on ${data.device}${data.launched ? " and opened" : ""}.`;
        toast(msg);
        if (log) log.textContent += "\n" + msg + (data.tv ? " It's on the TV's home screen / apps list." : "");
      } else {
        toast(data.error, "fail");
        if (log) log.textContent += "\nNot installed: " + data.error;
      }
    }
  }

  function bindBuildPage(el) {
    $$("[data-install]", el).forEach((x) => x.onclick = () => openDevices(+x.dataset.install));
    const dv = $("#devicesBtn", el); if (dv) dv.onclick = () => openDevices(null);
    $$("[data-show]", el).forEach((x) => x.onclick = async () => { const r = await api("show_in_folder", x.dataset.show); if (!r.ok) toast(r.error, "fail"); });
    $$("[data-rebuild]", el).forEach((x) => x.onclick = () => startBuild(+x.dataset.rebuild));
    $$("[data-log]", el).forEach((x) => x.onclick = () => { S.watchBuild = +x.dataset.log; go("builds"); });
    $$("[data-unqueue]", el).forEach((x) => x.onclick = async () => { await api("cancel_build", +x.dataset.unqueue); go("builds"); });
    const c = $("#cancelBuild", el);
    if (c) c.onclick = async () => { c.disabled = true; c.textContent = "Cancelling…"; await api("cancel_build", +c.dataset.id); };
    const log = $("#buildLog", el); if (log) log.scrollTop = log.scrollHeight;
  }

  function onBuildEvent(event, data) {
    if (event === "build_queue") { if (S.page === "builds") go("builds"); return; }
    if (event === "build_done") {
      toast(data.status === "success" ? "APK ready in " + data.seconds + " s." : data.message || "Build stopped.", data.status === "success" ? "ok" : data.status === "failed" ? "fail" : "warn");
      S.watchBuild = data.id;
      if (S.page === "builds") go("builds");
      if (S.page === "batch" && $("#modal").hidden) go("batch");
      return;
    }
    if (S.page !== "builds") return;
    if (event === "build_log") {
      const log = $("#buildLog"); if (!log) return;
      const atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
      log.textContent += (log.textContent ? "\n" : "") + data.line;
      if (atEnd) log.scrollTop = log.scrollHeight;
    } else if (event === "build_stage") {
      $$("#stages li").forEach((li, i) => { li.className = i < data.index ? "done" : i === data.index ? "now" : ""; });
      const bar = $("#buildBar"); if (bar) bar.style.width = Math.round((data.index / data.total) * 100) + "%";
    }
  }

  // ================================================================ BATCH BUILD
  // A whole folder of games -> apps. Kept apart from My Games and the ROM Library.
  const LOOKUP_CHIP = {
    waiting: ["", "Waiting"], looking: ["warn", "Looking up…"], found: ["ok", "Found"], manual: ["ok", "Chosen by you"],
    name: ["warn", "Matched by name"], notfound: ["", "Not found"], skipped: ["", "Skipped"], error: ["fail", "Problem"],
  };
  const METHOD = { fingerprint: "by fingerprint", serial: "by disc/cartridge code", name: "by file name", manual: "chosen by you" };
  S.batchTarget = S.batchTarget || "phone";
  S.batchFilter = S.batchFilter || "all";
  S.batchSearch = S.batchSearch || "";

  function batchFilterOf(i) {
    if (i.build_status === "success") return "built";
    if (i.build_status === "failed" || i.build_status === "cancelled") return "failed";
    if (i.build_status === "queued" || i.build_status === "running") return "queue";
    return { found: "found", manual: "found", name: "name", notfound: "notfound", skipped: "skipped", error: "skipped" }[i.state] || "waiting";
  }

  pages.batch = async (el, opts) => {
    if (opts.folder || S.batchFolder) return batchFolderPage(el, opts.folder || S.batchFolder);
    const folders = await api("batch_list_folders");
    el.innerHTML = `
      <div class="page-head"><div><h1>Batch Build</h1>
        <p class="lede">Turn a whole folder of games into apps. Tirso works out which game each file is, finds its details,
        description, pictures and logo, and makes an app icon from the logo on a white background. Games here stay separate from My Games.</p></div>
        <div class="row"><div class="seg" id="bTarget" title="Which version of the apps to build">
          <button data-t="phone" class="${S.batchTarget === "phone" ? "on" : ""}">Phones</button>
          <button data-t="tv" class="${S.batchTarget === "tv" ? "on" : ""}">TV</button></div>
          <button class="btn primary" id="bAdd">Choose a folder</button></div></div>
      ${folders.length ? `<div class="panel" style="padding:6px 22px">${folders.map((f) => {
        const busy = f.status === "scanning";
        const done = f.total - f.waiting;
        return `<div class="list-row">
          <div class="grow"><b>${esc(f.name)}</b> <span class="faint small">${f.target === "tv" ? "TV" : "Phones"}</span>
            <div class="faint small ellipsis" title="${esc(f.path)}">${esc(f.path)}</div>
            <div class="row" style="gap:6px;margin-top:6px;flex-wrap:wrap">
              <span class="chip">${plural(f.total, "game")}</span>
              ${f.found ? `<span class="chip ok">${f.found} found</span>` : ""}
              ${f.by_name ? `<span class="chip warn">${f.by_name} matched by name</span>` : ""}
              ${f.not_found ? `<span class="chip">${f.not_found} not found</span>` : ""}
              ${f.skipped ? `<span class="chip">${f.skipped} skipped</span>` : ""}
              ${f.built ? `<span class="chip ok">${f.built} built</span>` : ""}
              ${f.in_queue ? `<span class="chip warn">${f.in_queue} building</span>` : ""}
              ${f.failed ? `<span class="chip fail">${f.failed} failed</span>` : ""}
            </div>
            ${busy ? `<div class="row" style="gap:10px;margin-top:8px"><div class="progress" style="width:200px"><i style="width:${f.total ? Math.round(done * 100 / f.total) : 0}%"></i></div>
              <span class="faint small">Looking up ${done} of ${f.total}…</span></div>` : ""}
            ${f.message ? `<div class="hint" style="margin-top:6px">${esc(f.message)}</div>` : ""}
          </div>
          <button class="btn" data-open="${f.id}">Open</button>
          ${busy ? `<button class="btn ghost small" data-stop="${f.id}">Stop</button>` : `<button class="btn ghost small" data-again="${f.id}">Look up again</button>`}
          <button class="btn ghost small danger" data-rm="${f.id}">Remove</button></div>`; }).join("")}</div>`
        : `<div class="empty"><h3>No folders yet</h3>Choose a folder with your game files. Sub-folders are included, and
          files that aren't games are skipped. Nothing is built until you press Build.</div>`}`;
    $$("#bTarget button", el).forEach((b) => b.onclick = () => { S.batchTarget = b.dataset.t; go("batch"); });
    $("#bAdd", el).onclick = async () => {
      const r = await api("batch_pick_folder", S.batchTarget);
      if (r && r.ok) { toast(r.added ? `Found ${plural(r.added, "game file")}. Looking them up…` : "No new game files in that folder.", r.added ? "ok" : "warn"); S.batchFolder = r.id; go("batch"); }
      else if (r && r.error) toast(r.error, "fail");
    };
    $$("[data-open]", el).forEach((b) => b.onclick = () => { S.batchFolder = +b.dataset.open; go("batch"); });
    $$("[data-stop]", el).forEach((b) => b.onclick = async () => { await api("batch_stop_scan", +b.dataset.stop); go("batch"); });
    $$("[data-again]", el).forEach((b) => b.onclick = async () => { const r = await api("batch_rescan", +b.dataset.again); if (!r.ok) toast(r.error, "fail"); go("batch"); });
    $$("[data-rm]", el).forEach((b) => b.onclick = async () => {
      if (!await confirmBox("Remove this folder from Batch Build?", "Only Tirso's list is cleared. Your game files and any apps already built stay where they are.", "Remove")) return;
      const r = await api("batch_remove_folder", +b.dataset.rm);
      if (!r.ok) toast(r.error, "fail");
      go("batch");
    });
  };

  async function batchFolderPage(el, folderId) {
    const folders = await api("batch_list_folders");
    const f = folders.find((x) => x.id === folderId);
    if (!f) { S.batchFolder = null; return pages.batch(el, {}); }
    S.batchFolder = folderId;
    const items = await api("batch_items", folderId);
    const counts = {};
    items.forEach((i) => { const k = batchFilterOf(i); counts[k] = (counts[k] || 0) + 1; });
    const filters = [["all", "All", items.length], ["found", "Found"], ["name", "Matched by name"], ["notfound", "Not found"],
      ["waiting", "Looking up"], ["skipped", "Skipped"], ["queue", "Building"], ["built", "Built"], ["failed", "Failed"]]
      .map(([k, t, n]) => [k, t, n != null ? n : counts[k] || 0]).filter(([k, , n]) => k === "all" || n);
    if (!filters.some(([k]) => k === S.batchFilter)) S.batchFilter = "all";
    const busy = f.status === "scanning";
    const done = f.total - f.waiting;
    el.innerHTML = `
      <button class="btn ghost small" id="bBack" style="margin-bottom:10px">‹ All folders</button>
      <div class="page-head"><div><h1>${esc(f.name)}</h1><p class="lede ellipsis" title="${esc(f.path)}">${esc(f.path)}</p></div>
        <div class="row"><div class="seg" id="bTarget" title="Which version of the apps to build">
          <button data-t="phone" class="${f.target === "phone" ? "on" : ""}">Phones</button>
          <button data-t="tv" class="${f.target === "tv" ? "on" : ""}">TV</button></div>
          ${f.in_queue ? `<button class="btn" id="bStopBuilds">Stop building</button>` : ""}
          <button class="btn primary" id="bBuild"></button></div></div>
      ${busy ? `<div class="panel" style="padding:14px 18px;margin-bottom:14px"><div class="row" style="gap:12px"><div class="progress" style="width:260px"><i style="width:${f.total ? Math.round(done * 100 / f.total) : 0}%"></i></div>
        <span class="muted">Looking up ${done} of ${f.total}. You can review the games already found while the rest are looked up.</span>
        <span class="spacer"></span><button class="btn ghost small" id="bStop">Stop</button></div></div>` : ""}
      ${f.message ? `<div class="notice" style="margin-bottom:14px"><div>${esc(f.message)}</div></div>` : ""}
      <div class="panel" style="padding:14px 18px;margin-bottom:14px"><div class="row" style="gap:12px">
        <div class="grow"><b>Settings for every game in this folder</b><div class="muted small">${esc(batchSettingsText(f.settings))}</div></div>
        <button class="btn" id="bSettings">Change</button></div></div>
      <div class="toolbar">
        <div class="filters">${filters.map(([k, label, n]) => `<button class="filter ${S.batchFilter === k ? "on" : ""}" data-f="${k}">${esc(label)} <span class="faint">${n}</span></button>`).join("")}</div>
        <span class="spacer"></span>
        <input class="search" id="bSearch" placeholder="Search by name" value="${esc(S.batchSearch)}">
        ${busy ? "" : `<button class="btn ghost" id="bAgain" title="Look for new files, and look up games that weren't found">Look up again</button>`}
      </div>
      <div class="panel" style="padding:8px 10px"><div class="table-wrap"><table>
        <thead><tr><th style="width:28px"><input type="checkbox" id="bAll" aria-label="Tick all shown games"></th><th style="width:52px"></th>
          <th style="width:30%">App name</th><th>Console</th><th style="width:30%">Game found</th><th>Status</th><th></th></tr></thead>
        <tbody id="bRows"></tbody></table></div></div>`;

    const buildable = (i) => ["found", "name", "manual", "notfound"].includes(i.state);
    const draw = () => {
      const q = S.batchSearch.toLowerCase();
      const shown = items.filter((i) => (S.batchFilter === "all" || batchFilterOf(i) === S.batchFilter)
        && (!q || ((i.title || "") + " " + i.filename + " " + (i.match_name || "")).toLowerCase().includes(q)));
      $("#bRows").innerHTML = shown.slice(0, 600).map((i) => {
        const [lk, lt] = LOOKUP_CHIP[i.state] || ["", i.state];
        const [bk, bt] = i.build_status ? STATUS_CHIP[i.build_status] || ["", i.build_status] : [lk, lt];
        const icon = i.icon ? `<img src="${i.icon}" alt="" style="width:40px;height:40px;border-radius:10px;display:block;border:1px solid var(--line)">`
          : `<div style="width:40px;height:40px;border-radius:10px;display:flex;align-items:center;justify-content:center;background:color-mix(in srgb, ${i.color} 35%, var(--ink));font:600 13px var(--display)">${esc(initials(i.title || i.filename))}</div>`;
        const msg = i.build_status === "failed" ? i.build_message : i.message;
        return `<tr>
          <td><input type="checkbox" data-inc="${i.id}" ${i.include && buildable(i) ? "checked" : ""} ${buildable(i) ? "" : "disabled"} aria-label="Build ${esc(i.title || i.filename)}"></td>
          <td>${icon}</td>
          <td><b>${esc(i.title || i.filename)}</b><div class="faint small ellipsis" title="${esc(i.path)}">${esc(i.filename)} · ${esc(i.size_h)}</div></td>
          <td>${i.console ? `<span class="badge" style="--c:${i.color}">${esc(i.console_short)}</span>` : `<span class="faint">?</span>`}</td>
          <td>${i.match_name ? `${esc(i.match_name)}<div class="faint small">${esc(METHOD[i.method] || "")}${i.has_logo ? " · logo icon" : i.info_dir ? " · box art icon" : ""}</div>` : `<span class="faint">${i.state === "looking" || i.state === "waiting" ? "…" : "No match"}</span>`}</td>
          <td><span class="chip ${bk}" title="${esc(msg || "")}"><span class="dot"></span>${bt}</span>${msg && i.state !== "found" ? `<div class="faint small" style="max-width:260px;margin-top:4px">${esc(msg)}</div>` : ""}</td>
          <td style="text-align:right;white-space:nowrap">
            ${i.info_dir ? `<button class="btn ghost small" data-det="${i.id}">Details</button>` : ""}
            ${i.console && !["waiting", "looking", "skipped"].includes(i.state) ? `<button class="btn ghost small" data-fix="${i.id}">${i.match_name ? "Wrong game?" : "Find game"}</button>` : ""}
            ${buildable(i) ? `<button class="btn ghost small" data-ren="${i.id}">Rename</button>` : ""}
            ${i.build_id && i.build_status !== "queued" ? `<button class="btn ghost small" data-blog="${i.build_id}">Log</button>` : ""}
            ${i.apk_path && i.build_status === "success" ? `<button class="btn ghost small" data-show="${esc(i.apk_path)}">Show APK</button>` : ""}
          </td></tr>`;
      }).join("") || `<tr><td colspan="7" class="muted">${items.length ? "No games match." : busy ? "Reading the folder…" : "No game files in this folder."}</td></tr>`;
      if (shown.length > 600) $("#bRows").insertAdjacentHTML("beforeend", `<tr><td colspan="7" class="faint">Showing the first 600. Use the search or filters to see the rest.</td></tr>`);
      const all = $("#bAll", el);
      const ticks = shown.filter(buildable);
      all.checked = ticks.length > 0 && ticks.every((i) => i.include);
      all.onchange = async () => { await api("batch_set_include", ticks.map((i) => i.id), all.checked); ticks.forEach((i) => i.include = all.checked ? 1 : 0); draw(); };
      $$("[data-inc]", el).forEach((cb) => cb.onchange = async () => {
        const it = items.find((x) => x.id === +cb.dataset.inc); it.include = cb.checked ? 1 : 0;
        await api("batch_set_include", [it.id], cb.checked); updateBuild();
      });
      $$("[data-det]", el).forEach((b) => b.onclick = () => batchDetails(+b.dataset.det));
      $$("[data-fix]", el).forEach((b) => b.onclick = () => batchFix(items.find((x) => x.id === +b.dataset.fix)));
      $$("[data-ren]", el).forEach((b) => b.onclick = () => batchRename(items.find((x) => x.id === +b.dataset.ren)));
      $$("[data-blog]", el).forEach((b) => b.onclick = () => { S.watchBuild = +b.dataset.blog; go("builds"); });
      $$("[data-show]", el).forEach((b) => b.onclick = () => api("show_in_folder", b.dataset.show));
      updateBuild();
    };
    const updateBuild = () => {
      const n = items.filter((i) => i.include && buildable(i) && !["queued", "running"].includes(i.build_status)).length;
      const b = $("#bBuild", el);
      b.textContent = n ? `Build ${plural(n, "app")}` : "Build";
      b.disabled = !n;
    };
    $("#bBack", el).onclick = () => { S.batchFolder = null; go("batch"); };
    $("#bSettings", el).onclick = () => batchSettings(f);
    $$("#bTarget button", el).forEach((b) => b.onclick = async () => { await api("batch_set_target", folderId, b.dataset.t); go("batch"); });
    $$(".filter", el).forEach((b) => b.onclick = () => { S.batchFilter = b.dataset.f; go("batch"); });
    $("#bSearch", el).oninput = (e) => { S.batchSearch = e.target.value; draw(); };
    if ($("#bStop", el)) $("#bStop", el).onclick = async () => { await api("batch_stop_scan", folderId); go("batch"); };
    if ($("#bAgain", el)) $("#bAgain", el).onclick = async () => { const r = await api("batch_rescan", folderId); if (!r.ok) toast(r.error, "fail"); else if (r.added) toast(`${plural(r.added, "new game file")} found.`); go("batch"); };
    if ($("#bStopBuilds", el)) $("#bStopBuilds", el).onclick = async () => { const r = await api("batch_stop_builds", folderId); toast(`${plural(r.stopped, "waiting build")} removed from the queue.`, "warn"); go("batch"); };
    $("#bBuild", el).onclick = async () => {
      const n = items.filter((i) => i.include && buildable(i) && !["queued", "running"].includes(i.build_status)).length;
      if (n > 1 && !await confirmBox(`Build ${plural(n, "app")}?`, `They're built one after another (${f.target === "tv" ? "TV versions where the console has one" : "phone versions"}). You can keep using Tirso meanwhile, and stop the rest at any time.`, "Build")) return;
      const r = await api("batch_build_all", folderId);
      if (r.queued) toast(`${plural(r.queued, "app")} added to the build queue.`);
      if (r.problems && r.problems.length) toast(`${plural(r.problems.length, "game")} can't be built: ${r.problems[0].error}`, "warn");
      else if (!r.ok) toast(r.error, "fail");
      go("batch");
    };
    draw();
  }

  function batchSettingsText(st) {
    const prof = (S.profiles.find((p) => p.id === st.profile) || {}).name || st.profile;
    return [prof + (st.profile === "quality" && st.resolution !== "auto" ? " " + st.resolution : ""),
      st.widescreen ? "widescreen on" : "widescreen off",
      "DS: " + ({ rotate: "turns with the phone", stacked: "stacked screens", side: "side by side", large: "large top screen" })[st.ds_layout],
      "Wii: " + ({ sideways: "Wii Remote sideways", nunchuk: "Wii Remote + Nunchuk", classic: "Classic Controller", gamecube: "GameCube controller" })[st.wii_controls],
      "GameCube/Wii graphics: " + (st.graphics === "opengl" ? "OpenGL ES" : "Vulkan"),
      "other games: " + ({ landscape: "landscape", portrait: "portrait", auto: "follow the phone" })[st.orientation]].join(" · ");
  }

  // One Performance setup for the whole folder (games you set up yourself in My Games keep theirs).
  function batchSettings(f) {
    const st = Object.assign({}, f.settings);
    const m = $("#modal");
    const seg = (id, key, opts) => `<div class="seg" id="${id}">${opts.map(([k, t]) => `<button data-v="${k}" class="${st[key] === k ? "on" : ""}">${esc(t)}</button>`).join("")}</div>`;
    m.innerHTML = `<div class="modal wide" role="dialog" aria-modal="true" aria-label="Settings for every game"><h2>Settings for every game</h2>
      <p class="small">Used for every game built from this folder. Games you set up yourself in My Games keep their own settings.</p>
      <h3>Performance profile</h3>
      <div class="profiles">${S.profiles.map((p) => `<button class="profile ${st.profile === p.id ? "on" : ""}" data-p="${p.id}"><h3>${esc(p.name)}</h3><p>${esc(p.desc)}</p></button>`).join("")}</div>
      <div id="bsRes" ${st.profile === "quality" ? "" : "hidden"}><h3 style="margin-top:14px">Resolution (N64, PSP, PS2, GameCube, Wii)</h3>
        ${seg("bsResSeg", "resolution", [["auto", "Auto"], ["720p", "720p"], ["1080p", "1080p"], ["1440p", "1440p"]])}</div>
      <label class="toggle-row" style="margin-top:14px" for="bsWide"><span class="grow"><h3>Widescreen hack</h3><p class="small">For consoles that have one (N64, PSP, PS2, GameCube, Wii).</p></span><input type="checkbox" id="bsWide" class="switch" ${st.widescreen ? "checked" : ""}></label>
      <h3 style="margin-top:14px">GameCube and Wii graphics</h3>${seg("bsGfx", "graphics", [["vulkan", "Vulkan"], ["opengl", "OpenGL ES"]])}
      <h3 style="margin-top:14px">DS screens</h3>${seg("bsDs", "ds_layout", [["rotate", "Turn with the phone"], ["stacked", "Stacked"], ["side", "Side by side"], ["large", "Large top screen"]])}
      <h3 style="margin-top:14px">Wii controller</h3>${seg("bsWii", "wii_controls", [["sideways", "Remote sideways"], ["nunchuk", "Remote + Nunchuk"], ["classic", "Classic"], ["gamecube", "GameCube"]])}
      <h3 style="margin-top:14px">Screen orientation (other consoles)</h3>${seg("bsOr", "orientation", [["landscape", "Landscape"], ["portrait", "Portrait"], ["auto", "Follow the phone"]])}
      <div class="row" style="justify-content:flex-end;margin-top:18px"><button class="btn" data-a="no">Cancel</button><button class="btn primary" id="bsSave">Save</button></div></div>`;
    m.hidden = false;
    const segBind = (id, key) => $$(`#${id} button`, m).forEach((b) => b.onclick = () => { st[key] = b.dataset.v; $$(`#${id} button`, m).forEach((x) => x.classList.toggle("on", x === b)); });
    segBind("bsResSeg", "resolution"); segBind("bsGfx", "graphics"); segBind("bsDs", "ds_layout"); segBind("bsWii", "wii_controls"); segBind("bsOr", "orientation");
    $$(".profile", m).forEach((b) => b.onclick = () => { st.profile = b.dataset.p; $$(".profile", m).forEach((x) => x.classList.toggle("on", x === b)); $("#bsRes", m).hidden = st.profile !== "quality"; });
    m.onclick = async (e) => {
      if (e.target.id === "bsSave") {
        st.widescreen = $("#bsWide", m).checked;
        await api("batch_set_settings", f.id, st);
        m.hidden = true; toast("Saved. These settings are used for the next builds."); go("batch");
      } else if (e.target.dataset.a === "no" || e.target === m) m.hidden = true;
    };
  }

  async function batchDetails(itemId) {
    const d = await api("batch_item_detail", itemId);
    if (!d || !d.info) return;
    const i = d.info, p = d.pictures;
    const facts = [["Released", [i.month ? new Date(2000, i.month - 1).toLocaleString(undefined, { month: "short" }) : "", i.year].filter(Boolean).join(" ")],
      ["Genre", i.genre], ["Players", i.players], ["Developer", i.developer], ["Publisher", i.publisher], ["Region", i.region]].filter(([, v]) => v);
    const m = $("#modal");
    m.innerHTML = `<div class="modal wide" role="dialog" aria-modal="true" aria-label="Game details">
      <div style="display:grid;grid-template-columns:190px minmax(0,1fr);gap:22px;align-items:start">
        <div>${p.box ? `<img src="${p.box}" alt="Box art" style="width:100%;border-radius:6px">` : ""}
          <div class="row" style="gap:10px;margin-top:12px">${p.icon ? `<div><img src="${p.icon}" alt="App icon" style="width:72px;height:72px;border-radius:18px"><div class="faint small">App icon</div></div>` : ""}</div></div>
        <div style="min-width:0">
          <h2 style="margin:0">${esc(i.title)}</h2><div class="faint small" style="margin-top:4px">${esc(i.name)}</div>
          <div class="row" style="gap:6px;flex-wrap:wrap;margin-top:12px">${facts.map(([k, v]) => `<span class="chip">${esc(k)}: ${esc(v)}</span>`).join("")}</div>
          <p style="color:var(--text);line-height:1.55;margin-top:14px">${esc(i.description || "No description was found.")}</p>
          ${i.description_source ? `<div class="faint small">Description: ${esc(i.description_source)}${i.source_url ? ` · <a href="#" data-url="${esc(i.source_url)}">open page</a>` : ""}</div>` : ""}
          <div class="row" style="gap:10px;margin-top:14px">${["title", "snap"].filter((k) => p[k]).map((k) => `<img src="${p[k]}" alt="Screenshot" style="width:48%;border-radius:8px;border:1px solid var(--line)">`).join("")}</div>
        </div></div>
      <div class="row" style="justify-content:flex-end;margin-top:16px"><button class="btn primary" data-a="close">Close</button></div></div>`;
    m.hidden = false;
    m.onclick = (e) => {
      if (e.target.dataset.url) { e.preventDefault(); api("open_url", e.target.dataset.url); return; }
      if (e.target.dataset.a === "close" || e.target === m) m.hidden = true;
    };
  }

  function batchRename(it) {
    const m = $("#modal");
    m.innerHTML = `<div class="modal" role="dialog" aria-modal="true"><h2>App name</h2>
      <p>The name shown under the icon on the phone.</p>
      <label class="small faint" for="renIn">Name</label>
      <input class="search" id="renIn" style="width:100%;margin-top:4px" maxlength="50" value="${esc(it.title || "")}">
      <div class="row" style="justify-content:flex-end;margin-top:16px"><button class="btn" data-a="no">Cancel</button><button class="btn primary" data-a="yes">Save</button></div></div>`;
    m.hidden = false;
    $("#renIn", m).focus();
    m.onclick = async (e) => {
      const a = e.target.dataset.a;
      if (a === "yes") { const r = await api("batch_rename", it.id, $("#renIn", m).value); if (!r.ok) return toast(r.error, "fail"); m.hidden = true; go("batch"); }
      else if (a === "no" || e.target === m) m.hidden = true;
    };
  }

  function batchFix(it) {
    const m = $("#modal");
    m.innerHTML = `<div class="modal wide" role="dialog" aria-modal="true"><h2>Which game is this?</h2>
      <p class="ellipsis">${esc(it.filename)}</p>
      <label class="small faint" for="fixIn">Search the ${esc(it.console_short || "")} game list</label>
      <input class="search" id="fixIn" style="width:100%;margin-top:4px" value="${esc((it.title || "").replace(/:/g, ""))}">
      <div id="fixList" class="list" style="margin-top:10px;max-height:46vh;overflow-y:auto"></div>
      <div class="row" style="justify-content:flex-end;margin-top:16px"><button class="btn" data-a="no">Cancel</button></div></div>`;
    m.hidden = false;
    const input = $("#fixIn", m);
    let t = null;
    const search = async () => {
      const names = await api("batch_search", it.id, input.value);
      $("#fixList", m).innerHTML = names.map((n) => `<div class="list-row"><div class="grow">${esc(n)}</div><button class="btn small" data-pick="${esc(n)}">This one</button></div>`).join("")
        || `<div class="faint" style="padding:10px 0">No games found. Try fewer words.</div>`;
    };
    input.oninput = () => { clearTimeout(t); t = setTimeout(search, 250); };
    input.focus(); search();
    m.onclick = async (e) => {
      if (e.target.dataset.pick) { await api("batch_set_match", it.id, e.target.dataset.pick); m.hidden = true; toast("Looking up the details…"); go("batch"); return; }
      if (e.target.dataset.a === "no" || e.target === m) m.hidden = true;
    };
  }

  // ================================================================ SETTINGS
  pages.settings = async (el) => {
    const s = await api("get_settings");
    el.innerHTML = `
      <div class="page-head"><div><h1>Settings</h1></div></div>
      <div class="panel" style="max-width:760px"><div class="list">
        <div class="list-row"><div class="grow"><h3>APK output folder</h3><div class="muted small ellipsis">${esc(s.output_dir)}</div></div>
          <button class="btn ghost" id="openOut">Open</button><button class="btn" id="chOut">Change</button></div>
        <div class="list-row"><div class="grow"><h3>Tirso data folder</h3><div class="muted small ellipsis">${esc(s.data_dir)}</div>
          <div class="faint small">Library database, BIOS copies and icons. Kept outside the program folder so your files never end up in shared code.</div></div>
          <button class="btn ghost" id="openData">Open</button></div>
        <div class="list-row"><div class="grow"><h3>Signing key</h3><div class="muted small">${s.key ? "Every APK is signed with your own private key. Back it up somewhere safe: without it, phones can't update game apps you've already installed." : "Created automatically with your first APK."}</div></div>
          ${s.key ? `<button class="btn" id="backupKey">Back up key</button>` : ""}</div>
        <div class="list-row"><div class="grow"><h3>Appearance</h3><div class="muted small">Light or dark look for Tirso Compiler. "System" follows Windows.</div></div>
          <div class="seg" id="themeSeg">${[["light", "Light"], ["dark", "Dark"], ["system", "System"]].map(([k, t]) => `<button data-th="${k}" class="${s.theme === k ? "on" : ""}">${t}</button>`).join("")}</div></div>
        <div class="list-row"><div class="grow"><h3>Version</h3><div class="muted small">${esc(s.version)}</div></div></div>
      </div></div>`;
    $("#openOut", el).onclick = () => api("open_folder", "output");
    $("#openData", el).onclick = () => api("open_folder", "data");
    $("#chOut", el).onclick = async () => { await api("pick_output_dir"); go("settings"); };
    const bk = $("#backupKey", el); if (bk) bk.onclick = backupKey;
    $$("#themeSeg button", el).forEach((b) => b.onclick = async () => { await api("set_theme", b.dataset.th); applyTheme(b.dataset.th); go("settings"); });
  };

  // Light / dark look. Remembered by Tirso (and by the window, so it opens in the right colours).
  const themeQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;
  function applyTheme(theme) {
    S.theme = theme;
    const light = theme === "light" || (theme === "system" && themeQuery && themeQuery.matches);
    document.documentElement.dataset.theme = light ? "light" : "dark";
    try { localStorage.setItem("tirso-theme", theme); } catch (e) { /* not available */ }
  }
  if (themeQuery) themeQuery.addEventListener("change", () => { if (S.theme === "system") applyTheme("system"); });
  try { applyTheme(localStorage.getItem("tirso-theme") || "dark"); } catch (e) { applyTheme("dark"); }

  // ---------------------------------------------------------------- start
  (async function start() {
    api("get_settings").then((st) => { if (st && st.theme) applyTheme(st.theme); });
    const c = await api("get_consoles");
    S.consoles = c.consoles; S.profiles = c.profiles;
    S.consoles.forEach((k) => (S.byId[k.id] = k));
    S.allExt = Array.from(new Set(S.consoles.flatMap((k) => k.extensions)));
    go("dashboard");
  })();
})();
