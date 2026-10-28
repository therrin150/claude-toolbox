// A stand-in for Obsidian's Templater, enough to run the toolbox's vault templates against a folder on disk.
//
//   node fake_templater.js <template> <vault> <answers.json> <today YYYY-MM-DD>
//
// <answers.json> lists what the user would answer, in order: the text for each tp.system.prompt (null takes its
// default) and the chosen value for each tp.system.suggester. User scripts load from the vault's Templates/scripts/.
// Prints {"moved_to", "output", "notices"}; "output" is null when the template returned early.

const fs = require("fs");
const path = require("path");

const [templatePath, vault, answersPath, today] = process.argv.slice(2);
const answers = JSON.parse(fs.readFileSync(answersPath, "utf8"));
const notices = [];

function answer(kind) {
  if (answers.length === 0) throw new Error(`no answer left for a ${kind}`);
  return answers.shift();
}

function walk(folder) {
  const found = [];
  for (const entry of fs.readdirSync(folder, { withFileTypes: true })) {
    const full = path.join(folder, entry.name);
    if (entry.isDirectory()) found.push(...walk(full));
    else if (entry.name.endsWith(".md")) found.push(full);
  }
  return found;
}

function frontmatter(text) {
  const match = text.match(/^---\n([\s\S]*?)\n---/);
  if (!match) return null;
  const fields = {};
  for (const line of match[1].split("\n")) {
    const pair = line.match(/^([A-Za-z_]+):\s*(.*)$/);
    if (pair) fields[pair[1]] = pair[2].replace(/^['"]|['"]$/g, "");
  }
  return fields;
}

globalThis.Notice = class {
  constructor(message) { notices.push(message); }
};

globalThis.app = {
  vault: {
    getAbstractFileByPath: (p) => (fs.existsSync(path.join(vault, p)) ? { path: p } : null),
    createFolder: async (p) => fs.mkdirSync(path.join(vault, p)),
    create: async (p, text) => fs.writeFileSync(path.join(vault, p), text, { flag: "wx" }),
    getMarkdownFiles: () => walk(vault).map((full) => ({ path: path.relative(vault, full), extension: "md" })),
  },
  metadataCache: {
    getFileCache: (file) => ({ frontmatter: frontmatter(fs.readFileSync(path.join(vault, file.path), "utf8")) }),
  },
};

let movedTo = null;
const [year, month] = today.split("-");
const user = {};
const scripts = path.join(vault, "Templates", "scripts");
if (fs.existsSync(scripts)) {
  for (const name of fs.readdirSync(scripts).filter((n) => n.endsWith(".js"))) {
    user[name.replace(/\.js$/, "")] = require(path.join(scripts, name));
  }
}

const tp = {
  system: {
    prompt: async (message, fallback) => {
      const value = answer(`prompt "${message}"`);
      return value === null ? fallback : value;
    },
    suggester: async (labels, values) => {
      const value = answer("suggester");
      if (!values.includes(value)) throw new Error(`${JSON.stringify(value)} isn't offered: ${values}`);
      return value;
    },
  },
  date: { now: (format) => format.replace("YYYY", year).replace("MM", month).replace("DD", today.split("-")[2]) },
  file: { move: async (p) => { movedTo = p; } },
  user,
};

// Templater's tags: <%* code %> runs, <% expr %> prints, and a closing -%> eats the newline after it.
function compile(text) {
  const lines = ["let tR = '';"];
  let last = 0;
  let trim = false;
  for (const match of text.matchAll(/<%(\*?)([\s\S]*?)(-?)%>/g)) {
    let chunk = text.slice(last, match.index);
    if (trim) chunk = chunk.replace(/^\n/, "");
    lines.push(`tR += ${JSON.stringify(chunk)};`);
    lines.push(match[1] === "*" ? match[2] : `tR += (${match[2]});`);
    trim = match[3] === "-";
    last = match.index + match[0].length;
  }
  let tail = text.slice(last);
  if (trim) tail = tail.replace(/^\n/, "");
  lines.push(`tR += ${JSON.stringify(tail)};`, "return tR;");
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  return new AsyncFunction("tp", "app", lines.join("\n"));
}

(async () => {
  const output = await compile(fs.readFileSync(templatePath, "utf8"))(tp, globalThis.app);
  const result = typeof output === "string" ? output : null;
  if (result !== null && movedTo) fs.writeFileSync(path.join(vault, `${movedTo}.md`), result);
  console.log(JSON.stringify({ moved_to: movedTo, output: result, notices, answers_left: answers.length }));
})().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(1);
});
