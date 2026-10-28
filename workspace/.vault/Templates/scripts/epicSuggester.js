/**
 * Templater user script: pick a parent epic from the vault's task records.
 *
 * Copied into this vault by claude-toolbox's install.py. Edit the toolbox's
 * workspace/.vault/Templates/scripts/epicSuggester.js, never this copy: the next sync overwrites it.
 *
 * Usage inside a template:   const picked = await tp.user.epicSuggester(tp, "Tasks");
 *
 * Lists every `type: epic` record in the tasks folder that isn't `done`, newest key first, plus a choice to type a
 * key (an epic filed outside this vault). Returns {key, link}, where link is a wikilink to the epic's record (or the
 * bare key when typed), or null when cancelled.
 */

async function epicSuggester(tp, tasksFolder) {
  const prefix = `${tasksFolder}/`;
  const records = app.vault.getMarkdownFiles()
    .filter((file) => file.path.startsWith(prefix))
    .map((file) => ({ file, fields: app.metadataCache.getFileCache(file)?.frontmatter || {} }));

  const epics = records
    .filter(({ fields }) => fields.type === "epic" && fields.key && fields.status !== "done")
    .map(({ file, fields }) => ({
      key: String(fields.key),
      label: fields.summary ? `${fields.key}: ${fields.summary}` : String(fields.key),
      link: `[[${file.path.replace(/\.md$/, "")}|${fields.key}]]`,
    }))
    .sort((a, b) => b.key.localeCompare(a.key, undefined, { numeric: true }));

  // A typed key starts from the prefix the records use, when they have one.
  const keyed = records.find(({ fields }) => fields.key);
  const keyPrefix = keyed ? String(keyed.fields.key).replace(/\d+$/, "") : "";
  const typed = async () => {
    const key = ((await tp.system.prompt("Parent epic key", keyPrefix)) || "").trim();
    return key && key !== keyPrefix ? { key, link: key } : null;
  };

  if (epics.length === 0) {
    new Notice(`No open epic records in ${tasksFolder}/: type the key.`);
    return typed();
  }

  const OTHER = "__other__";
  const choice = await tp.system.suggester(
    epics.map((epic) => epic.label).concat("Other: type a key"),
    epics.map((epic) => epic.key).concat(OTHER),
    false,
    "Parent epic"
  );
  if (choice === OTHER) {
    return typed();
  }
  const epic = epics.find((candidate) => candidate.key === choice);
  return epic ? { key: epic.key, link: epic.link } : null;
}

module.exports = epicSuggester;
