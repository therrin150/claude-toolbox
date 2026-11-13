/* A small in-house syntax highlighter: no third-party code. Line at a time, carrying block-comment and multi-line
   string state between lines so a diff side can be highlighted as a stream. Classes map to CSS tokens:
   tk-c comment, tk-s string, tk-k keyword, tk-n number, tk-a annotation, tk-t type, tk-g tag, tk-r attribute, tk-y key. */
window.LRHighlight = (() => {
  "use strict";

  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const words = (s) => new Set(s.split(/\s+/).filter(Boolean));

  const KEYWORDS = {
    java: words(`abstract assert boolean break byte case catch char class const continue default do double else enum
      extends final finally float for goto if implements import instanceof int interface long native new package
      private protected public return short static strictfp super switch synchronized this throw throws transient try
      void volatile while var record yield sealed permits true false null`),
    kotlin: words(`as break class continue do else false for fun if in interface is null object package return super
      this throw true try typealias typeof val var when while by catch constructor delegate dynamic field file finally
      get import init param property receiver set setparam where actual abstract annotation companion const crossinline
      data enum expect external final infix inline inner internal lateinit noinline open operator out override private
      protected public reified sealed suspend tailrec vararg`),
    js: words(`async await break case catch class const continue debugger default delete do else export extends false
      finally for function if import in instanceof let new null of return static super switch this throw true try
      typeof undefined var void while with yield interface type enum implements private protected public readonly as`),
    python: words(`and as assert async await break class continue def del elif else except False finally for from
      global if import in is lambda None nonlocal not or pass raise return True try while with yield self`),
    shell: words(`if then else elif fi for while until do done case esac function in return local export set unset
      echo exit source readonly declare true false`),
    sql: words(`select from where and or not insert into values update set delete create table alter drop index on
      primary key foreign references join left right inner outer full cross group by order having limit offset as
      distinct union all case when then else end null is in exists between like default constraint unique check
      begin commit rollback grant revoke view sequence if cascade returning with varchar integer bigint boolean text
      timestamp serial`),
    yaml: words(`true false null yes no on off ~`),
    json: words(`true false null`),
  };

  // Each language: an ordered list of [sticky regex, class or function(match) -> class, optional state to enter].
  const CLIKE = (kw) => [
    [/\/\*/y, "tk-c", "block"],
    [/\/\/.*/y, "tk-c"],
    [/"""/y, "tk-s", "tquote"],
    [/"(?:\\.|[^"\\])*"?/y, "tk-s"],
    [/'(?:\\.|[^'\\])*'?/y, "tk-s"],
    [/`(?:\\.|[^`\\])*`?/y, "tk-s"],
    [/@[A-Za-z_][\w.]*/y, "tk-a"],
    [/\b(?:0[xX][\da-fA-F_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?[lLfFdD]?)\b/y, "tk-n"],
    [/[A-Za-z_$][\w$]*/y, (m) => (kw.has(m) ? "tk-k" : /^[A-Z][a-z0-9]/.test(m) ? "tk-t" : null)],
  ];

  const LANGS = {
    java: CLIKE(KEYWORDS.java),
    kotlin: CLIKE(KEYWORDS.kotlin),
    js: CLIKE(KEYWORDS.js),
    python: [
      [/#.*/y, "tk-c"],
      [/"""/y, "tk-s", "tquote"],
      [/'''/y, "tk-s", "tsquote"],
      [/[rbfu]*"(?:\\.|[^"\\])*"?/y, "tk-s"],
      [/[rbfu]*'(?:\\.|[^'\\])*'?/y, "tk-s"],
      [/@[A-Za-z_][\w.]*/y, "tk-a"],
      [/\b\d[\d_]*(?:\.\d+)?\b/y, "tk-n"],
      [/[A-Za-z_]\w*/y, (m) => (KEYWORDS.python.has(m) ? "tk-k" : /^[A-Z][a-z0-9]/.test(m) ? "tk-t" : null)],
    ],
    sql: [
      [/--.*/y, "tk-c"],
      [/\/\*/y, "tk-c", "block"],
      [/'(?:''|[^'])*'?/y, "tk-s"],
      [/\b\d+(?:\.\d+)?\b/y, "tk-n"],
      [/[A-Za-z_]\w*/y, (m) => (KEYWORDS.sql.has(m.toLowerCase()) ? "tk-k" : null)],
    ],
    yaml: [
      [/#.*/y, "tk-c"],
      [/^(\s*-?\s*)?[^\s#:'"][^:#]*?(?=:(\s|$))/y, "tk-y"],
      [/"(?:\\.|[^"\\])*"?/y, "tk-s"],
      [/'(?:''|[^'])*'?/y, "tk-s"],
      [/\$\{[^}]*\}/y, "tk-a"],
      [/\b\d+(?:\.\d+)?\b/y, "tk-n"],
      [/[A-Za-z_~][\w-]*/y, (m) => (KEYWORDS.yaml.has(m.toLowerCase()) ? "tk-k" : null)],
    ],
    json: [
      [/"(?:\\.|[^"\\])*"(?=\s*:)/y, "tk-y"],
      [/"(?:\\.|[^"\\])*"?/y, "tk-s"],
      [/-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/y, "tk-n"],
      [/[a-z]+/y, (m) => (KEYWORDS.json.has(m) ? "tk-k" : null)],
    ],
    properties: [
      [/^\s*[#!].*/y, "tk-c"],
      [/^\s*[^=:\s]+/y, "tk-y"],
      [/\$\{[^}]*\}/y, "tk-a"],
    ],
    xml: [
      [/<!--/y, "tk-c", "xcomment"],
      [/<\/?[\w:.-]+/y, "tk-g"],
      [/\/?>/y, "tk-g"],
      [/[\w:.-]+(?==)/y, "tk-r"],
      [/"[^"]*"?/y, "tk-s"],
      [/'[^']*'?/y, "tk-s"],
      [/\$\{[^}]*\}/y, "tk-a"],
    ],
    shell: [
      [/#.*/y, "tk-c"],
      [/"(?:\\.|[^"\\])*"?/y, "tk-s"],
      [/'[^']*'?/y, "tk-s"],
      [/\$\{?[\w@#?*!-]+\}?/y, "tk-a"],
      [/\b\d+\b/y, "tk-n"],
      [/[A-Za-z_][\w-]*/y, (m) => (KEYWORDS.shell.has(m) ? "tk-k" : null)],
    ],
    bru: [
      [/\/\/.*/y, "tk-c"],
      [/^\s*[\w:-]+(?=\s*[{:])/y, "tk-y"],
      [/\{\{[^}]*\}\}/y, "tk-a"],
    ],
  };

  // Closers for multi-line states.
  const CLOSERS = { block: /\*\//g, tquote: /"""/g, tsquote: /'''/g, xcomment: /-->/g };

  const EXT = {
    java: "java", groovy: "java", gradle: "java", scala: "java", kt: "kotlin", kts: "kotlin",
    js: "js", mjs: "js", cjs: "js", ts: "js", tsx: "js", jsx: "js",
    py: "python", sql: "sql", yml: "yaml", yaml: "yaml", json: "json",
    properties: "properties", env: "properties", conf: "properties",
    xml: "xml", pom: "xml", html: "xml", htm: "xml", svg: "xml", xsd: "xml",
    sh: "shell", bash: "shell", zsh: "shell", bru: "bru",
  };

  function langFor(path) {
    const name = (path || "").split("/").pop().toLowerCase();
    if (name === "dockerfile" || name.endsWith(".dockerfile")) return "shell";
    const ext = name.includes(".") ? name.split(".").pop() : "";
    return EXT[ext] || null;
  }

  function wrap(cls, text) {
    return cls ? `<span class="${cls}">${esc(text)}</span>` : esc(text);
  }

  /* Highlights one line. state is null or a CLOSERS key; returns { html, state } for the next line. */
  function line(lang, text, state) {
    const rules = LANGS[lang];
    if (!rules) return { html: esc(text), state: null };
    let out = "";
    let pos = 0;
    let plain = "";
    const flush = () => {
      if (plain) {
        out += esc(plain);
        plain = "";
      }
    };
    while (pos < text.length) {
      if (state) {
        const closer = CLOSERS[state];
        closer.lastIndex = pos;
        const found = closer.exec(text);
        const end = found ? found.index + found[0].length : text.length;
        const cls = state === "block" || state === "xcomment" ? "tk-c" : "tk-s";
        flush();
        out += wrap(cls, text.slice(pos, end));
        pos = end;
        if (found) state = null;
        continue;
      }
      let matched = false;
      for (const [re, kind, enter] of rules) {
        if (re.source.startsWith("^") && pos !== 0) continue;
        re.lastIndex = pos;
        const m = re.exec(text);
        if (!m || m[0].length === 0) continue;
        const cls = typeof kind === "function" ? kind(m[0]) : kind;
        flush();
        out += wrap(cls, m[0]);
        pos += m[0].length;
        if (enter) state = enter;
        matched = true;
        break;
      }
      if (!matched) {
        plain += text[pos];
        pos += 1;
      }
    }
    flush();
    return { html: out, state };
  }

  return { langFor, line, esc };
})();
