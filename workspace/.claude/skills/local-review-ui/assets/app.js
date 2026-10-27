/* local-review-ui page: a GitHub-style review of a local change, with line comments a Claude session reads and
   answers. Vanilla JS, no dependencies; talks only to the local server that served it. */
(() => {
  "use strict";

  const HL = window.LRHighlight;
  const esc = HL.esc;
  const enc = encodeURIComponent;
  const POLL_MS = 3000;
  const API_TIMEOUT_MS = 30000;

  const local = {
    get(key) {
      try { return localStorage.getItem(key); } catch (e) { return null; }
    },
    set(key, value) {
      try { localStorage.setItem(key, value); } catch (e) { /* private window or blocked storage */ }
    },
  };

  // --- small helpers ------------------------------------------------------------------------------------------------

  function el(tag, attrs, ...kids) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "html") node.innerHTML = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (key === "dataset") Object.assign(node.dataset, value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false) continue;
      node.append(kid.nodeType ? kid : String(kid));
    }
    return node;
  }

  async function api(method, url, body) {
    const options = { method, headers: {} };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    // A request that never answers would otherwise hold the poll's in-flight guard forever.
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), API_TIMEOUT_MS);
    options.signal = abort.signal;
    let response;
    try {
      response = await fetch(url, options);
    } catch (e) {
      throw new Error(e.name === "AbortError" ? "The server didn't answer in time" : e.message);
    } finally {
      clearTimeout(timer);
    }
    let data = null;
    try { data = await response.json(); } catch (e) { /* empty body */ }
    if (!response.ok) {
      const error = new Error((data && data.error) || `${response.status} ${response.statusText}`);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function toast(message, kind = "info", ms = 7000) {
    const box = document.getElementById("toasts");
    const node = el("div", { class: `toast ${kind}` }, message);
    box.append(node);
    setTimeout(() => node.remove(), ms);
  }

  function ago(iso) {
    if (!iso) return "";
    const seconds = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
    if (seconds < 60) return "just now";
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return new Date(iso).toLocaleDateString();
  }

  function inline(text) {
    return esc(text)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\n/g, "<br>");
  }

  function markdown(text) {
    return String(text || "").split("```").map((part, i) => {
      if (i % 2 === 1) return `<pre><code>${esc(part.replace(/^[\w+-]*\n/, ""))}</code></pre>`;
      return part.split(/\n{2,}/).map((p) => (p.trim() ? `<p>${inline(p.trim())}</p>` : "")).join("");
    }).join("");
  }

  const resumeHint = () => (S.review && S.review.resumeHint) || null;

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      // No clipboard API (an insecure context, or permission denied): the old way, through a selection.
      const area = el("textarea", { style: "position:fixed;opacity:0" });
      area.value = text;
      document.body.append(area);
      area.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch (err) { /* blocked */ }
      area.remove();
      return ok;
    }
  }

  // The prompt a reviewer pastes into a new session, shown wherever the page warns that nobody is listening.
  function resumePrompt(hint) {
    const button = el("button", { class: "btn small", type: "button", title: "Copy to the clipboard", onclick: async (e) => {
      e.stopPropagation();
      const ok = await copyText(hint);
      button.textContent = ok ? "Copied" : "Copy failed";
      setTimeout(() => { button.textContent = "Copy"; }, 1500);
    } }, "Copy");
    return el("span", { class: "resume" }, "Paste into a new Claude session: ", el("code", { title: hint }, hint), button);
  }

  const ICON_OPEN = '<svg viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M1.5 3A1.5 1.5 0 0 1 3 1.5h10A1.5 1.5 0 0 1 14.5 3v7a1.5 1.5 0 0 1-1.5 1.5H8.2L5 14.2V11.5H3A1.5 1.5 0 0 1 1.5 10z"/></svg>';
  const ICON_RESOLVED = '<svg viewBox="0 0 16 16" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.2" d="M1.5 3A1.5 1.5 0 0 1 3 1.5h10A1.5 1.5 0 0 1 14.5 3v7a1.5 1.5 0 0 1-1.5 1.5H8.2L5 14.2V11.5H3A1.5 1.5 0 0 1 1.5 10z"/><path class="chk" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M5 6.6l2 2 4-4"/></svg>';
  const isOpen = (c) => c.status === "open";
  const lastAuthor = (c) => (c.replies && c.replies.length ? c.replies[c.replies.length - 1].author : c.author);
  const awaitingYou = (c) => isOpen(c) && lastAuthor(c) === "claude";
  const awaitingClaude = (c) => isOpen(c) && lastAuthor(c) !== "claude";

  // --- state --------------------------------------------------------------------------------------------------------

  const S = {
    id: null,
    review: null,
    repo: null,
    files: [],
    filesFp: null,
    baseRef: null,
    path: null,
    diff: null,
    data: { comments: [], rounds: [], version: -1 },
    openSeq: 0,
    filesSeq: 0,
    polling: false,
    inflight: {},
    viewed: {},
    round: 1,
    listener: null,
    offline: false,
    mode: local.get("lrui-mode") === "split" ? "split" : "unified",
    closeOnDone: local.get("lrui-close-on-done") !== "false",
    full: false,
    filter: "",
    composer: null,
    lastClick: null,
    drafts: {},
    replyOpen: {},
    expanded: {},
    editing: null,
    closedAt: null,
    pending: false,
  };

  const reviewUrl = (suffix = "") => `/api/reviews/${enc(S.id)}${suffix}`;
  const fileComments = (repo, path) => S.data.comments.filter((c) => c.repo === repo && c.path === path);
  const viewedKey = (path) => `${S.repo}:${path}`;
  const isViewed = (file) => {
    const mark = S.viewed[viewedKey(file.path)];
    return mark === true || (mark && mark === file.sig);
  };

  // --- boot ---------------------------------------------------------------------------------------------------------

  function buildShell() {
    document.getElementById("app").replaceWith(
      el("div", { class: "app", id: "app" },
        el("header", { class: "top", id: "hdr" }),
        el("div", { class: "body" },
          el("aside", { class: "files", id: "side" }),
          el("main", { class: "content", id: "main" }))),
      el("div", { class: "toasts", id: "toasts" }),
    );
    const main = document.getElementById("main");
    main.addEventListener("focusout", () => setTimeout(() => {
      if (S.pending && !typing()) renderMain();
    }, 0));
    document.addEventListener("keydown", onKey);
  }

  function parseHash() {
    const params = new URLSearchParams(location.hash.slice(1));
    return { repo: params.get("repo"), file: params.get("file") };
  }

  function setHash() {
    const params = new URLSearchParams();
    if (S.repo) params.set("repo", S.repo);
    if (S.path) params.set("file", S.path);
    history.replaceState(null, "", `#${params.toString()}`);
  }

  async function init() {
    buildShell();
    try {
      S.review = await api("GET", reviewUrl());
    } catch (e) {
      document.getElementById("main").append(el("div", { class: "empty" }, e.message));
      renderHeader();
      return;
    }
    document.title = `${S.id} · Local Review`;
    S.listener = S.review.listener;
    const wanted = parseHash();
    const repo = S.review.repos.find((r) => r.name === wanted.repo) || S.review.repos[0];
    S.repo = repo ? repo.name : null;
    await loadComments();
    renderHeader();
    if (S.repo) await loadFiles(wanted.file);
    setInterval(poll, POLL_MS);
  }

  async function loadComments() {
    const result = await api("GET", reviewUrl("/comments"));
    S.data = { comments: result.comments || [], rounds: result.rounds || [], version: result.version };
    S.viewed = result.viewed || {};
    S.round = S.data.rounds.length + 1;
    S.closedAt = result.closedAt || null;
  }

  async function loadFiles(preferPath) {
    const repo = S.review.repos.find((r) => r.name === S.repo);
    if (repo && repo.error) {
      S.filesSeq++;
      S.files = [];
      renderSidebar();
      setMain(el("div", { class: "empty" }, `${repo.name}: ${repo.error}`));
      return;
    }
    // Sequenced like openFile: a poll's reload that lands after a repo switch or a newer reload is dropped.
    const seq = ++S.filesSeq;
    const pathAtStart = S.path;
    const result = await api("GET", reviewUrl(`/files?repo=${enc(S.repo)}`));
    if (seq !== S.filesSeq) return;
    // The user picked another file while this was in flight: keep their pick rather than jump back.
    if (S.path !== pathAtStart) preferPath = S.path;
    S.files = result.files;
    S.filesFp = result.fingerprint;
    S.baseRef = result.baseRef;
    const has = (p) => p && S.files.some((f) => f.path === p);
    const target = has(preferPath) ? preferPath : has(S.path) ? S.path : (S.files[0] || {}).path;
    renderSidebar();
    if (target) await openFile(target, { keepScroll: target === S.path });
    else {
      S.path = null;
      S.diff = null;
      setHash();
      renderMain(true);
    }
  }

  async function openFile(path, { keepScroll = false } = {}) {
    const changed = path !== S.path;
    S.path = path;
    if (changed) {
      S.composer = null;
      S.full = false;
    }
    // A slow diff that lands after a later click must not render under the later file's header.
    const seq = ++S.openSeq;
    const file = S.files.find((f) => f.path === path);
    let diff = null;
    if (file) {
      try {
        diff = await api("GET", reviewUrl(`/diff?repo=${enc(S.repo)}&path=${enc(path)}${S.full ? "&context=full" : ""}`));
      } catch (e) {
        if (seq === S.openSeq) toast(e.message, "err");
      }
    }
    if (seq !== S.openSeq) return;
    S.diff = diff;
    setHash();
    renderSidebarList();
    renderMain(true, keepScroll);
    if (!keepScroll) document.getElementById("main").scrollTop = 0;
  }

  // --- header -------------------------------------------------------------------------------------------------------

  function renderHeader() {
    const hdr = document.getElementById("hdr");
    hdr.replaceChildren();
    hdr.append(el("div", { class: "brand" }, "Local Review", el("span", { class: "id" }, S.id || "")));
    if (!S.review) return;

    const repos = S.review.repos;
    const label = (r) => {
      const attn = S.data.comments.filter((c) => c.repo === r.name && awaitingYou(c)).length;
      return `${r.name}${r.branch ? ` — ${r.branch}` : ""}${attn ? ` (${attn} awaiting you)` : ""}`;
    };
    if (repos.length > 1) {
      const select = el("select", { title: "Repository", onchange: async (e) => {
        S.repo = e.target.value;
        S.path = null;
        S.composer = null;
        await loadFiles();
        renderHeader();
      } }, repos.map((r) => el("option", { value: r.name, selected: r.name === S.repo }, label(r))));
      hdr.append(select);
    } else if (repos.length === 1) {
      hdr.append(el("strong", {}, repos[0].name));
    }
    const repo = repos.find((r) => r.name === S.repo);
    if (repo && repo.branch) {
      hdr.append(el("span", { class: "branch" }, el("code", {}, repo.branch), ` vs ${repo.baseRef} @ ${repo.mergeBase}`));
    }
    hdr.append(el("span", { class: "spacer" }));
    hdr.append(el("span", { id: "listener" }));
    renderListener();
    const dark = document.documentElement.dataset.theme !== "light";
    if (S.closedAt) {
      hdr.append(el("span", { class: "badge", title: `Closed ${new Date(S.closedAt).toLocaleString()}` }, "Review closed"));
      hdr.append(el("button", { class: "btn ghost small", title: "Switch theme", onclick: toggleTheme }, dark ? "Light mode" : "Dark mode"));
      hdr.append(el("button", { class: "btn", onclick: reopen }, "Reopen review"));
      return;
    }
    hdr.append(el("span", { class: "badge", title: "Comments you add now belong to this round" }, `Round ${S.round}`));
    hdr.append(el("button", { class: "btn ghost small", title: "Switch theme", onclick: toggleTheme }, dark ? "Light mode" : "Dark mode"));
    hdr.append(el("button", { class: "btn primary", onclick: openFinish }, "Finish review"));
  }

  function renderListener() {
    const slot = document.getElementById("listener");
    if (!slot) return;
    const l = S.listener;
    let text;
    let on = false;
    let title = "";
    if (S.offline) {
      // A string is the server's own error (it answered); true means it didn't answer at all.
      text = typeof S.offline === "string" ? S.offline : "Server offline — retrying";
    } else if (l && l.fresh) {
      on = true;
      text = "Claude session listening";
      title = `Session ${l.session}${l.until ? `, until ${new Date(l.until).toLocaleTimeString()}` : ""}`;
    } else {
      text = "No session listening";
      title = "Finishing still records the round; tell your Claude session, and it will find the comments.";
    }
    const hint = !S.offline && !on ? resumeHint() : null;
    slot.replaceChildren(el("span", { class: "listener", title }, el("span", { class: `dot${on ? " on" : ""}` }), text,
      hint ? [" — ", resumePrompt(hint)] : null));
  }

  async function reopen() {
    try {
      await api("POST", reviewUrl("/reopen"));
      await loadComments();
      renderHeader();
      const listening = S.listener && S.listener.fresh;
      const hint = resumeHint();
      toast(listening ? "Review reopened."
        : hint ? el("span", {}, "Review reopened. No Claude session is listening. ", resumePrompt(hint))
          : "Review reopened. No Claude session is listening: ask your session to pick the review back up.",
      "ok", listening || !hint ? 10000 : 20000);
    } catch (e) {
      toast(e.message, "err");
    }
  }

  function toggleTheme() {
    const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    local.set("lrui-theme", next);
    renderHeader();
  }

  // --- sidebar ------------------------------------------------------------------------------------------------------

  function renderSidebar() {
    const side = document.getElementById("side");
    const input = el("input", { type: "search", placeholder: "Filter files", value: S.filter, oninput: (e) => {
      S.filter = e.target.value.toLowerCase();
      renderSidebarList();
    } });
    side.replaceChildren(
      el("div", { class: "files-head" }, el("div", { class: "totals", id: "totals" }), input),
      el("ul", { class: "file-list", id: "flist" }),
      el("div", { id: "others" }),
    );
    renderSidebarList();
  }

  function renderSidebarList() {
    const list = document.getElementById("flist");
    if (!list) return;
    const adds = S.files.reduce((n, f) => n + f.additions, 0);
    const dels = S.files.reduce((n, f) => n + f.deletions, 0);
    const seen = S.files.filter(isViewed).length;
    document.getElementById("totals").replaceChildren(
      `${S.files.length} file${S.files.length === 1 ? "" : "s"} · `,
      el("span", { class: "plus" }, `+${adds}`), " ", el("span", { class: "minus" }, `−${dels}`),
      ` · ${seen}/${S.files.length} viewed`,
    );

    list.replaceChildren(...S.files
      .filter((f) => !S.filter || f.path.toLowerCase().includes(S.filter))
      .map((f) => {
        const parts = f.path.split("/");
        const name = parts.pop();
        const comments = fileComments(S.repo, f.path);
        const open = comments.filter(isOpen).length;
        const attn = comments.filter(awaitingYou).length;
        const resolved = comments.length - open;
        return el("li", {
          class: `file-item${f.path === S.path ? " active" : ""}${isViewed(f) ? " viewed" : ""}`,
          title: f.oldPath !== f.path ? `${f.oldPath} → ${f.path}` : f.path,
          onclick: () => openFile(f.path),
        },
        el("span", { class: `st st-${f.status}` }, f.status),
        el("span", { class: "name" }, el("span", { class: "fname" }, name), parts.length ? el("span", { class: "dir" }, `‎${parts.join("/")}`) : null),
        el("span", { class: "meta" },
          open ? el("span", { class: `cmt-count${attn ? " attn" : ""}`, title: attn ? `${attn} awaiting your reply, ${open} open` : `${open} open`, html: `${ICON_OPEN}${open}` }) : null,
          resolved ? el("span", { class: "cmt-count resolved", title: `${resolved} resolved`, html: `${ICON_RESOLVED}${resolved}` }) : null,
          f.binary ? el("span", { class: "muted" }, "bin") : [el("span", { class: "plus" }, `+${f.additions}`), el("span", { class: "minus" }, `−${f.deletions}`)],
          isViewed(f) ? el("span", { class: "seen", title: "Viewed" }, "✓") : null));
      }));

    const changed = new Set(S.files.map((f) => f.path));
    const elsewhere = S.data.comments.filter((c) => c.repo === S.repo && !changed.has(c.path));
    const others = document.getElementById("others");
    const paths = [...new Set(elsewhere.map((c) => c.path))];
    others.replaceChildren(paths.length ? el("div", { class: "side-section" },
      el("h4", {}, "Comments on files no longer changed"),
      paths.map((p) => el("div", {}, el("a", { href: "#", onclick: (e) => {
        e.preventDefault();
        openFile(p);
      } }, p), ` (${elsewhere.filter((c) => c.path === p).length})`))) : "");
  }

  // --- main ---------------------------------------------------------------------------------------------------------

  function typing() {
    const active = document.activeElement;
    const main = document.getElementById("main");
    return active && active.tagName === "TEXTAREA" && main.contains(active) && active.value.trim() !== "";
  }

  function setMain(...nodes) {
    document.getElementById("main").replaceChildren(...nodes);
  }

  function renderMain(force = false, keepScroll = true) {
    if (!force && typing()) {
      S.pending = true;
      return;
    }
    S.pending = false;
    const main = document.getElementById("main");
    const scroll = main.scrollTop;
    if (!S.path) {
      setMain(el("div", { class: "empty" }, S.files.length ? "Pick a file." : "No changes against the base branch in this repo."));
      return;
    }
    const file = S.files.find((f) => f.path === S.path);
    const comments = fileComments(S.repo, S.path);
    const nodes = [fileHead(file)];

    if (!file) {
      nodes.push(el("div", { class: "notice" }, "This file has no changes against the base branch any more."));
      nodes.push(outdatedBox(comments, "Comments on this file"));
      setMain(...nodes);
      return;
    }
    const diff = S.diff;
    if (!diff) {
      setMain(...nodes, el("div", { class: "empty" }, "Loading…"));
      return;
    }
    const { placed, outdated } = anchor(comments, diff);
    if (diff.binary) nodes.push(el("div", { class: "notice" }, "Binary file; no diff to show."));
    else if (diff.truncated) nodes.push(el("div", { class: "notice warn" }, "This diff is too large to show here."));
    else if (!diff.hunks.length) nodes.push(el("div", { class: "notice" }, file.status === "A" ? "Empty new file." : "No content changes (mode or rename only)."));
    if (outdated.length) nodes.push(outdatedBox(outdated, "Comments that can't be placed in this diff"));
    if (diff.hunks.length) nodes.push(S.mode === "split" ? splitTable(diff, placed) : unifiedTable(diff, placed));
    setMain(...nodes);
    if (keepScroll) main.scrollTop = scroll;
    const box = main.querySelector(".composer textarea");
    if (box && S.composer && S.composer.focus) {
      S.composer.focus = false;
      box.focus();
      box.setSelectionRange(box.value.length, box.value.length);
    }
  }

  async function openInIde(path, line) {
    try {
      await api("POST", reviewUrl("/open-in-ide"), { repo: S.repo, path, line });
    } catch (e) {
      toast(e.message, "err");
    }
  }

  function editorLink(file, atLine) {
    const repo = S.review.repos.find((r) => r.name === S.repo);
    if (!repo || !file) return null;
    let line = atLine || 1;
    if (!atLine && S.diff && S.diff.hunks.length) {
      const first = S.diff.hunks[0].lines.find((l) => l.t === "add") || S.diff.hunks[0].lines.find((l) => l.n != null);
      if (first && first.n) line = first.n;
    }
    // A configured command opens the repo as a project with the file at the line; the URL scheme only opens the file.
    if (S.review.editorCommand) {
      return el("button", { class: "btn small", title: `Open ${S.repo} with ${file.path}:${line}`, onclick: (e) => {
        e.stopPropagation();
        openInIde(file.path, line);
      } }, "Open in IDE");
    }
    const template = S.review.editorUrl;
    if (!template) return null;
    const windows = repo.path.includes("\\");
    const absolute = windows ? `${repo.path}\\${file.path.replace(/\//g, "\\")}` : `${repo.path}/${file.path}`;
    const href = template.replace("{path}", enc(absolute).replace(/%2F/g, "/").replace(/%5C/g, "\\")).replace("{line}", String(line));
    return el("a", { class: "btn small", href, title: absolute }, "Open in IDE");
  }

  function fileHead(file) {
    const index = S.files.findIndex((f) => f.path === S.path);
    const go = (delta) => {
      const next = S.files[index + delta];
      if (next) openFile(next.path);
    };
    const pathNode = el("span", { class: "path" });
    if (file && file.oldPath !== file.path) pathNode.append(el("span", { class: "old" }, `${file.oldPath} → `));
    pathNode.append(S.path);
    const head = el("div", { class: "file-head" }, file ? el("span", { class: `st st-${file.status}` }, file.status) : null, pathNode);
    if (file) {
      head.append(el("span", {}, el("span", { class: "plus" }, `+${file.additions}`), " ", el("span", { class: "minus" }, `−${file.deletions}`)));
      head.append(el("span", { class: "spacer" }));
      head.append(el("span", { class: "seg" },
        el("button", { class: S.mode === "unified" ? "on" : "", onclick: () => setMode("unified") }, "Unified"),
        el("button", { class: S.mode === "split" ? "on" : "", onclick: () => setMode("split") }, "Split")));
      head.append(el("label", {}, el("input", { type: "checkbox", checked: S.full, onchange: async (e) => {
        S.full = e.target.checked;
        await openFile(S.path, { keepScroll: true });
      } }), "Full file"));
      head.append(el("label", {}, el("input", { type: "checkbox", checked: isViewed(file), onchange: (e) => setViewed(file, e.target.checked) }), "Viewed"));
      head.append(editorLink(file));
      head.append(el("button", { class: "btn small ghost", disabled: index <= 0, title: "Previous file ( [ )", onclick: () => go(-1) }, "‹"));
      head.append(el("button", { class: "btn small ghost", disabled: index < 0 || index >= S.files.length - 1, title: "Next file ( ] )", onclick: () => go(1) }, "›"));
    }
    return head;
  }

  function setMode(mode) {
    S.mode = mode;
    local.set("lrui-mode", mode);
    renderMain(true);
  }

  async function setViewed(file, viewed) {
    try {
      S.viewed = await api("PUT", reviewUrl("/viewed"), { repo: S.repo, path: file.path, viewed, fingerprint: file.sig });
      renderSidebarList();
      if (viewed) {
        // By path: a poll may have reloaded S.files during the await, so `file` is no longer in it.
        const at = S.files.findIndex((f) => f.path === file.path);
        const next = S.files.find((f, i) => i > at && !isViewed(f));
        if (next) openFile(next.path);
      }
    } catch (e) {
      toast(e.message, "err");
    }
  }

  // --- anchoring ----------------------------------------------------------------------------------------------------

  function sideMaps(diff) {
    const maps = { old: new Map(), new: new Map() };
    for (const hunk of diff.hunks) {
      for (const line of hunk.lines) {
        if (line.o != null) maps.old.set(line.o, line.s);
        if (line.n != null) maps.new.set(line.n, line.s);
      }
    }
    return maps;
  }

  /* The server places each comment against the file as it is now (scripts/anchors.py) and widens the diff to show it.
     A comment newer than the diff sits at its own line until the next poll fetches the diff again. */
  function anchor(comments, diff) {
    const maps = sideMaps(diff);
    const server = diff.anchors || {};
    const placed = new Map();
    const outdated = [];
    for (const c of comments) {
      let at = c.id in server ? server[c.id] : null;
      if (!(c.id in server) && maps[c.side].get(c.endLine) === c.anchorText) at = { line: c.endLine, moved: false, outdated: false };
      if (!at || !maps[c.side].has(at.line)) {
        outdated.push(c);
        continue;
      }
      const key = `${c.side}:${at.line}`;
      if (!placed.has(key)) placed.set(key, []);
      placed.get(key).push({ comment: c, moved: at.moved, outdated: at.outdated });
    }
    return { placed, outdated };
  }

  // A comment the diff hasn't placed yet: one just added here, or a note from the session.
  function unanchored() {
    return S.diff && fileComments(S.repo, S.path).some((c) => !(c.id in (S.diff.anchors || {})));
  }

  // --- diff tables --------------------------------------------------------------------------------------------------

  function highlighter() {
    const lang = HL.langFor(S.path);
    const state = { old: null, new: null };
    return (line) => {
      let html = esc(line.s);
      if (line.t !== "add") {
        const r = HL.line(lang, line.s, state.old);
        state.old = r.state;
        html = r.html;
      }
      if (line.t !== "del") {
        const r = HL.line(lang, line.s, state.new);
        state.new = r.state;
        html = r.html;
      }
      return html + (line.noNewline ? '<span class="nonl" title="No newline at end of file">⊘</span>' : "");
    };
  }

  function inSelection(side, num) {
    const c = S.composer;
    return c && c.path === S.path && c.repo === S.repo && c.side === side && num != null && num >= c.line && num <= c.endLine;
  }

  function numCell(side, num, kind) {
    return el("td", {
      class: `num ${kind || ""}${num != null ? " can" : ""}${inSelection(side, num) ? " selected" : ""}`,
      dataset: num != null ? { side, line: String(num) } : undefined,
    }, num != null ? String(num) : "");
  }

  function extraRows(keys, span, rows) {
    for (const key of keys) {
      const items = rowsPlaced.get(key);
      if (items && items.length) {
        rows.push(el("tr", { class: "thread-row" }, el("td", { colspan: String(span) }, items.map((item) => thread(item.comment, item)))));
      }
      const c = S.composer;
      if (c && c.path === S.path && c.repo === S.repo && key === `${c.side}:${c.endLine}`) {
        rows.push(el("tr", { class: "thread-row" }, el("td", { colspan: String(span) }, composer())));
      }
    }
  }

  let rowsPlaced = new Map();

  function hunkRow(hunk, span) {
    return el("tr", { class: "hunk" }, el("td", { colspan: String(span) },
      `@@ -${hunk.oldStart} +${hunk.newStart} @@ ${hunk.section || ""}`));
  }

  function unifiedTable(diff, placed) {
    rowsPlaced = placed;
    const rows = [];
    for (const hunk of diff.hunks) {
      // Lines between hunks are never seen, so comment/string state must not carry across a hunk boundary.
      const hl = highlighter();
      if (!(S.full && diff.hunks.length === 1)) rows.push(hunkRow(hunk, 4));
      for (const line of hunk.lines) {
        const side = line.t === "del" ? "old" : "new";
        const num = side === "old" ? line.o : line.n;
        const selected = inSelection(side, num);
        rows.push(el("tr", { class: `line ${line.t}${selected ? " selected" : ""}` },
          numCell("old", line.t === "add" ? null : line.o, ""),
          numCell("new", line.t === "del" ? null : line.n, ""),
          el("td", { class: "sign" }, line.t === "add" ? "+" : line.t === "del" ? "-" : " "),
          el("td", { class: "code", html: hl(line) })));
        const keys = line.t === "ctx" ? [`old:${line.o}`, `new:${line.n}`] : [`${side}:${num}`];
        extraRows(keys, 4, rows);
      }
    }
    const table = el("table", { class: "diff unified" },
      el("colgroup", {}, el("col", { class: "num" }), el("col", { class: "num" }), el("col", { class: "sign" }), el("col")),
      el("tbody", {}, rows));
    table.addEventListener("click", onNumClick);
    return table;
  }

  function splitRows(hunk) {
    const out = [];
    let dels = [];
    let adds = [];
    const flush = () => {
      for (let i = 0; i < Math.max(dels.length, adds.length); i++) out.push({ left: dels[i] || null, right: adds[i] || null });
      dels = [];
      adds = [];
    };
    for (const line of hunk.lines) {
      if (line.t === "del") {
        if (adds.length) flush();
        dels.push(line);
      } else if (line.t === "add") {
        adds.push(line);
      } else {
        flush();
        out.push({ left: line, right: line });
      }
    }
    flush();
    return out;
  }

  function splitTable(diff, placed) {
    rowsPlaced = placed;
    const rows = [];
    for (const hunk of diff.hunks) {
      // Lines between hunks are never seen, so comment/string state must not carry across a hunk boundary.
      const hlLeft = highlighter();
      const hlRight = highlighter();
      rows.push(hunkRow(hunk, 4));
      for (const { left, right } of splitRows(hunk)) {
        const lk = left ? (left.t === "ctx" ? "" : left.t) : "";
        const rk = right ? (right.t === "ctx" ? "" : right.t) : "";
        rows.push(el("tr", { class: "line" },
          numCell("old", left ? left.o : null, lk),
          el("td", { class: `code ${left ? lk : "empty"}${left && inSelection("old", left.o) ? " selected" : ""}`, html: left ? hlLeft({ ...left, t: left.t === "ctx" ? "del" : left.t }) : "" }),
          numCell("new", right ? right.n : null, rk),
          el("td", { class: `code ${right ? rk : "empty"}${right && inSelection("new", right.n) ? " selected" : ""}`, html: right ? hlRight({ ...right, t: right.t === "ctx" ? "add" : right.t }) : "" })));
        const keys = [];
        if (left) keys.push(`old:${left.o}`);
        if (right) keys.push(`new:${right.n}`);
        extraRows(keys, 4, rows);
      }
    }
    const table = el("table", { class: "diff split" },
      el("colgroup", {}, el("col", { class: "num" }), el("col"), el("col", { class: "num" }), el("col")),
      el("tbody", {}, rows));
    table.addEventListener("click", onNumClick);
    return table;
  }

  function onNumClick(event) {
    const cell = event.target.closest("td.num.can");
    if (!cell) return;
    let side = cell.dataset.side;
    const num = Number(cell.dataset.line);
    // In the unified view a context line's comment belongs to the new side, as on GitHub.
    if (S.mode === "unified" && !cell.closest("tr").classList.contains("del")) side = "new";
    const line = S.mode === "unified" && side === "new" ? Number(cell.closest("tr").children[1].dataset.line) : num;
    let start = line;
    let end = line;
    const last = S.lastClick;
    if (event.shiftKey && last && last.side === side && last.path === S.path && last.repo === S.repo) {
      start = Math.min(last.line, line);
      end = Math.max(last.line, line);
    } else {
      S.lastClick = { side, line, path: S.path, repo: S.repo };
    }
    S.composer = { repo: S.repo, path: S.path, side, line: start, endLine: end, focus: true };
    renderMain(true);
  }

  // --- comments -----------------------------------------------------------------------------------------------------

  function composer() {
    const c = S.composer;
    const key = `new:${c.repo}:${c.path}:${c.side}:${c.line}:${c.endLine}`;
    const box = el("textarea", { placeholder: "Leave a comment for Claude", oninput: (e) => { S.drafts[key] = e.target.value; } });
    box.value = S.drafts[key] || "";
    const cancel = () => {
      S.composer = null;
      renderMain(true);
    };
    // In-flight state lives in S, keyed like the draft: a re-render mid-POST builds a new composer with the same text.
    const submit = async () => {
      const body = box.value.trim();
      if (!body || S.inflight[key]) return;
      S.inflight[key] = true;
      const maps = sideMaps(S.diff);
      const map = maps[c.side];
      const texts = [];
      for (let n = c.line; n <= c.endLine; n++) if (map.has(n)) texts.push(map.get(n));
      try {
        await api("POST", reviewUrl("/comments"), {
          repo: c.repo, path: c.path, side: c.side, line: c.line, endLine: c.endLine,
          lineText: texts.join("\n"), anchorText: map.get(c.endLine) ?? "", body,
        });
        delete S.drafts[key];
        S.composer = null;
        await loadComments();
        renderMain(true);
        renderSidebarList();
      } catch (e) {
        toast(e.message, "err");
      } finally {
        delete S.inflight[key];
      }
    };
    box.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
      if (e.key === "Escape") cancel();
    });
    const range = c.line === c.endLine ? `line ${c.line}` : `lines ${c.line}–${c.endLine}`;
    return el("div", { class: "composer" },
      el("div", { class: "hint" }, `Comment on ${range} (${c.side === "old" ? "deleted side" : "new side"}) · shift-click another line number to extend`),
      box,
      el("div", { class: "row-end" },
        el("span", { class: "kbd" }, "⌘/Ctrl+Enter to comment · Esc to cancel"),
        el("button", { class: "btn", onclick: cancel }, "Cancel"),
        el("button", { class: "btn primary", onclick: submit }, "Comment")));
  }

  function message(author, body, at, tools) {
    const claude = author === "claude";
    return el("div", { class: "msg" },
      tools ? el("span", { class: "tools" }, tools) : null,
      el("div", { class: `who${claude ? " claude" : ""}` }, claude ? "Claude" : "You", el("span", { class: "when", title: at || "" }, ago(at))),
      el("div", { class: "text", html: markdown(body) }));
  }

  async function patchComment(c, change) {
    try {
      await api("PATCH", reviewUrl(`/comments/${enc(c.id)}`), change);
      await loadComments();
      renderMain(true);
      renderSidebarList();
    } catch (e) {
      toast(e.message, "err");
    }
  }

  function thread(c, { moved = false, outdated = false } = {}) {
    const resolved = c.status === "resolved";
    const collapsed = resolved && !S.expanded[c.id];
    const range = c.line === c.endLine ? `line ${c.line}` : `lines ${c.line}–${c.endLine}`;
    const node = el("div", { class: `thread${resolved ? " resolved" : ""}${collapsed ? " collapsed" : ""}` });

    const bar = el("div", { class: "thread-bar", onclick: () => {
      if (resolved) {
        S.expanded[c.id] = !S.expanded[c.id];
        renderMain(true);
      }
    } },
    el("span", { class: `pill ${c.status}` }, resolved ? "Resolved" : awaitingYou(c) && (c.replies || []).length ? "Claude replied" : "Open"),
    // A note Claude opened belongs to no round.
    c.round == null ? el("span", { class: "pill claude", title: "Claude opened this thread" }, "Opened by Claude")
      : el("span", { class: "pill round" }, `Round ${c.round}`),
    moved ? el("span", { class: "pill moved", title: `Originally ${range}` }, "Moved") : null,
    outdated ? el("span", { class: "pill outdated", title: `Originally ${range}; the code there changed` }, "Outdated") : null,
    el("span", {}, `${c.side === "old" ? "Deleted side, " : ""}${moved || outdated ? `originally ${range}` : range}`),
    el("span", { class: "spacer" }),
    c.side === "new" && !collapsed && S.review.editorCommand && S.files.some((f) => f.path === c.path)
      ? el("button", { class: "btn small ghost", title: "Open in IDE at this line", onclick: (e) => {
        e.stopPropagation();
        openInIde(c.path, c.line);
      } }, "Open in IDE")
      : null,
    collapsed ? el("span", {}, "Show") : null);
    node.append(bar);
    if (outdated && c.lineText && !collapsed) node.append(el("div", { class: "snippet" }, c.lineText));

    const msgs = el("div", { class: "msgs" });
    if (S.editing === c.id) {
      const box = el("textarea", {});
      box.value = c.body;
      msgs.append(el("div", { class: "msg" }, box, el("div", { class: "row-end" },
        el("button", { class: "btn", onclick: () => { S.editing = null; renderMain(true); } }, "Cancel"),
        el("button", { class: "btn primary", onclick: async () => {
          S.editing = null;
          await patchComment(c, { body: box.value });
        } }, "Save"))));
    } else {
      const editable = c.author === "user" && !(c.replies && c.replies.length);
      const tools = editable ? [
        el("button", { class: "btn small ghost", onclick: () => { S.editing = c.id; renderMain(true); } }, "Edit"),
        el("button", { class: "btn small ghost", onclick: async () => {
          try {
            await api("DELETE", reviewUrl(`/comments/${enc(c.id)}`));
            await loadComments();
            renderMain(true);
            renderSidebarList();
          } catch (e) {
            toast(e.message, "err");
          }
        } }, "Delete"),
      ] : null;
      msgs.append(message(c.author, c.body, c.createdAt, tools));
    }
    for (const reply of c.replies || []) msgs.append(message(reply.author, reply.body, reply.at));
    node.append(msgs);

    const actions = el("div", { class: "thread-actions" });
    const key = `reply:${c.id}`;
    if (S.replyOpen[c.id]) {
      const box = el("textarea", { placeholder: "Reply", oninput: (e) => { S.drafts[key] = e.target.value; } });
      box.value = S.drafts[key] || "";
      const send = async () => {
        const body = box.value.trim();
        if (!body || S.inflight[key]) return;
        S.inflight[key] = true;
        try {
          await api("POST", reviewUrl(`/comments/${enc(c.id)}/replies`), { body });
          delete S.drafts[key];
          S.replyOpen[c.id] = false;
          await loadComments();
          renderMain(true);
          renderSidebarList();
        } catch (e) {
          toast(e.message, "err");
        } finally {
          delete S.inflight[key];
        }
      };
      box.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send();
        if (e.key === "Escape") { S.replyOpen[c.id] = false; renderMain(true); }
      });
      actions.append(el("div", { class: "grow" }, box, el("div", { class: "row-end" },
        el("button", { class: "btn", onclick: () => { S.replyOpen[c.id] = false; renderMain(true); } }, "Cancel"),
        el("button", { class: "btn primary", onclick: send }, "Reply"))));
      setTimeout(() => box.focus(), 0);
    } else {
      actions.append(el("button", { class: "btn small", onclick: () => { S.replyOpen[c.id] = true; renderMain(true); } }, "Reply"));
    }
    actions.append(el("button", { class: "btn small", onclick: () => patchComment(c, { status: resolved ? "open" : "resolved" }) },
      resolved ? "Reopen" : "Resolve"));
    node.append(actions);
    return node;
  }

  function outdatedBox(comments, title) {
    if (!comments.length) return el("div");
    return el("div", { class: "outdated-box" }, el("h4", {}, title), comments.map((c) => {
      const t = thread(c);
      if (c.lineText) t.insertBefore(el("div", { class: "snippet" }, c.lineText), t.children[1]);
      return t;
    }));
  }

  // --- finish -------------------------------------------------------------------------------------------------------

  function openFinish() {
    const added = S.data.comments.filter((c) => c.round === S.round && c.author === "user").length;
    const forClaude = S.data.comments.filter(awaitingClaude).length;
    const l = S.listener;
    const summary = el("textarea", { placeholder: "Optional overall note for Claude" });
    const close = () => backdrop.remove();
    // The shortcut bypasses the disabled buttons, so a double press would otherwise record an extra empty round.
    let busy = false;
    const finish = async (done) => {
      if (busy) return;
      busy = true;
      button.disabled = true;
      doneButton.disabled = true;
      try {
        const result = await api("POST", reviewUrl("/submit"), { summary: summary.value, done });
        close();
        await loadComments();
        renderHeader();
        renderMain(true);
        const what = done ? "Review closed" : `Round ${result.round.n} finished`;
        const listening = result.listener && result.listener.fresh;
        if (done && listening && S.closeOnDone) {
          // A browser lets a page close only a tab with one history entry (as review.py open makes) or one a script
          // opened. When it refuses, the tab stays, and the timeout says so; a closed tab never runs it.
          window.close();
          setTimeout(() => toast(`${what}. The listening Claude session is picking it up; you can close this tab.`, "ok"), 300);
        } else if (listening) {
          toast(`${what}. The listening Claude session is picking it up.`, "ok");
        } else {
          const hint = resumeHint();
          toast(hint ? el("span", {}, `${what}. No Claude session is listening. `, resumePrompt(hint))
            : `${what}. No Claude session is listening: tell your session, and it will find the comments.`, "warn", hint ? 20000 : 12000);
        }
      } catch (e) {
        busy = false;
        button.disabled = false;
        doneButton.disabled = false;
        toast(e.message, "err");
      }
    };
    const button = el("button", { class: "btn primary", title: "Claude answers the comments, then listens for the next round", onclick: () => finish(false) }, "Finish round");
    const doneButton = el("button", { class: "btn", title: "Claude answers anything left, then stops listening", onclick: () => finish(true) }, "Done reviewing");
    const closeToggle = el("label", { title: "Only while a Claude session is listening; otherwise the tab stays to show the warning" },
      el("input", { type: "checkbox", checked: S.closeOnDone, onchange: (e) => {
        S.closeOnDone = e.target.checked;
        local.set("lrui-close-on-done", String(e.target.checked));
      } }), "Close tab when done");
    const backdrop = el("div", { class: "backdrop", onclick: (e) => { if (e.target === backdrop) close(); } },
      el("div", { class: "modal", role: "dialog", "aria-modal": "true" },
        el("h3", {}, `Finish review · round ${S.round}`),
        el("div", { class: "mbody" },
          el("div", { class: "stat" }, `${added} new comment${added === 1 ? "" : "s"} this round · ${forClaude} thread${forClaude === 1 ? "" : "s"} waiting on Claude`),
          el("div", { class: "stat" }, l && l.fresh
            ? "A Claude session is listening and wakes when you finish."
            : resumeHint() ? ["No Claude session is listening. The round is still recorded. ", resumePrompt(resumeHint())]
              : "No Claude session is listening. The round is still recorded; tell your session the review is done."),
          el("div", { class: "stat" }, el("strong", {}, "Finish round"), ": Claude answers the comments, then listens for your next round."),
          el("div", { class: "stat" }, el("strong", {}, "Done reviewing"), ": Claude answers anything left and stops listening. Neither is an approval: committing or publishing still waits for your instructions."),
          summary),
        el("div", { class: "mfoot" }, closeToggle, el("button", { class: "btn", onclick: close }, "Cancel"), doneButton, button)));
    summary.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) finish(false);
      if (e.key === "Escape") close();
    });
    document.body.append(backdrop);
    summary.focus();
  }

  // --- polling and keys ---------------------------------------------------------------------------------------------

  // setInterval doesn't wait for a slow poll; skip a tick rather than stack them.
  async function poll() {
    if (S.polling) return;
    S.polling = true;
    try {
      await pollOnce();
    } finally {
      S.polling = false;
    }
  }

  async function pollOnce() {
    let state;
    try {
      state = await api("GET", reviewUrl("/state"));
    } catch (e) {
      const offline = e.status ? `Server error: ${e.message}` : true;
      if (S.offline !== offline) {
        S.offline = offline;
        renderListener();
      }
      return;
    }
    const wasOffline = S.offline;
    S.offline = false;
    const shown = () => JSON.stringify([S.listener && S.listener.fresh, S.listener && S.listener.session, resumeHint()]);
    const before = shown();
    S.listener = state.listener;
    S.review.resumeHint = state.resumeHint || null;
    if (wasOffline || before !== shown()) renderListener();

    if (state.commentsVersion !== S.data.version) {
      const before = [S.round, S.closedAt];
      await loadComments();
      renderSidebarList();
      renderMain();
      if (S.round !== before[0] || S.closedAt !== before[1]) renderHeader();
    }
    const fp = state.fingerprints[S.repo];
    if (fp && S.filesFp && fp !== S.filesFp) {
      if (typing()) return;
      await loadFiles(S.path);
      toast("The files changed on disk; the diff is refreshed.");
    } else if (unanchored() && !typing()) {
      await openFile(S.path, { keepScroll: true });
    }
  }

  function onKey(event) {
    if (event.target.closest && event.target.closest("textarea, input, select")) return;
    if (event.key === "Escape" && S.composer) {
      S.composer = null;
      renderMain(true);
    }
    const index = S.files.findIndex((f) => f.path === S.path);
    if (event.key === "]" && S.files[index + 1]) openFile(S.files[index + 1].path);
    if (event.key === "[" && index > 0) openFile(S.files[index - 1].path);
  }

  // --- index page ---------------------------------------------------------------------------------------------------

  async function renderIndex() {
    document.title = "Local Review";
    const root = el("div", { class: "index" }, el("h1", {}, "Local reviews"));
    document.getElementById("app").replaceWith(root, el("div", { class: "toasts", id: "toasts" }));
    try {
      const reviews = await api("GET", "/api/reviews");
      if (!reviews.length) root.append(el("p", {}, "No reviews registered. A Claude session opens one with review.py open."));
      for (const r of reviews) {
        root.append(el("div", { class: "review-row" },
          el("a", { class: "rid", href: `/review/${enc(r.id)}` }, r.id),
          el("span", {}, r.repos.join(", ")),
          el("span", { class: "spacer" }),
          r.error ? el("span", { class: "muted" }, r.error) : el("span", { class: "muted" }, `${r.closed ? "closed · " : ""}${r.rounds} round${r.rounds === 1 ? "" : "s"} · ${r.open} open · updated ${ago(r.updatedAt)}`)));
      }
    } catch (e) {
      root.append(el("p", {}, e.message));
    }
  }

  const match = location.pathname.match(/^\/review\/([^/]+)\/?$/);
  if (match) {
    S.id = decodeURIComponent(match[1]);
    init();
  } else {
    renderIndex();
  }
})();
