import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const app = readFileSync(new URL("../app.html", import.meta.url), "utf8");
const core = readFileSync(new URL("../app_core.html", import.meta.url), "utf8");

function extractLine(source, signature) {
  const start = source.indexOf(signature);
  assert.notEqual(start, -1, `missing function: ${signature}`);
  const end = source.indexOf("\n", start);
  return source.slice(start, end);
}

function extractBlock(source, signature) {
  const start = source.indexOf(signature);
  assert.notEqual(start, -1, `missing function: ${signature}`);
  const end = source.indexOf("function pct(x){", start);
  assert.notEqual(end, -1, `unterminated function: ${signature}`);
  return source.slice(start, end);
}

const context = vm.createContext({
  __seasonCache: new WeakMap(),
  __progressCache: new WeakMap(),
  selectedTitleIds: new Set(),
  advancedFilters: {
    category: "all",
    status: "all",
    genre: "all",
    seasons: "all",
    favorite: "all",
    cover: "all",
    progress: "all",
  },
  esc: (value) => String(value ?? ""),
  coverMarkup: () => "",
  statusPT: (value) => String(value ?? ""),
  genreBadges: () => "",
  daysEstimate: () => null,
  getRate: () => 2,
  datePT: () => "",
  hasGenre: () => false,
  coverOf: () => "",
});

const functions = [
  extractLine(app, "function seasonEntries(x){"),
  extractBlock(app, "function seriesProgress(x){"),
  extractLine(app, "function pct(x){"),
  extractLine(app, "function isFilmTitle(x){"),
  extractLine(app, "function currentSeasonEpisodeTotal(x,season=x?.current_season){"),
  extractLine(app, "function franchiseLabel(x){"),
  extractLine(app, "function nextPosition(x){"),
  extractLine(app, "function card(x,priority=false){"),
  extractLine(app, "function applyAdvancedFilterToList(list){"),
];
vm.runInContext(functions.join("\n"), context);

const unknown = { id: "unknown", current_episode: 0 };
const zero = { id: "zero", current_episode: 0, episodes: 12 };
const inProgress = { id: "started", current_episode: 3, episodes: 12 };
const complete = { id: "complete", current_episode: 12, episodes: 12 };

assert.equal(context.pct(unknown), null);
assert.equal(context.pct(zero), 0);
assert.equal(context.pct(inProgress), 25);
assert.equal(context.pct(complete), 100);

context.advancedFilters.progress = "unknown";
assert.deepEqual(Array.from(context.applyAdvancedFilterToList([unknown, zero, inProgress, complete]), (x) => x.id), ["unknown"]);
context.advancedFilters.progress = "none";
assert.deepEqual(Array.from(context.applyAdvancedFilterToList([unknown, zero, inProgress, complete]), (x) => x.id), ["zero"]);
context.advancedFilters.progress = "complete";
assert.deepEqual(Array.from(context.applyAdvancedFilterToList([unknown, zero, inProgress, complete]), (x) => x.id), ["complete"]);

const missingSeasonCount = {
  id: "season-two-unknown",
  category: "Anime",
  current_season: 2,
  current_episode: 0,
  total_seasons: 2,
  total_episodes: 24,
  episodes: 24,
  seasons_json: JSON.stringify([{ season: 1, episodes: 12 }, { season: 2 }]),
};
assert.equal(context.currentSeasonEpisodeTotal(missingSeasonCount, 2), 0);
assert.equal(context.nextPosition(missingSeasonCount), null);
assert.doesNotMatch(context.card(missingSeasonCount), /T2 · Episódio 0\/24/);
assert.equal(context.currentSeasonEpisodeTotal({ current_season: 2, total_seasons: 2, episodes: 24 }, 2), 0);
const knownSeasonCount = {
  ...missingSeasonCount,
  seasons_json: JSON.stringify([{ season: 1, episodes: 12 }, { season: 2, episodes: 13 }]),
};
assert.equal(context.currentSeasonEpisodeTotal(knownSeasonCount, 2), 13);
assert.match(context.card(knownSeasonCount), /T2 · Episódio 0\/13/);
assert.doesNotMatch(context.card(knownSeasonCount), /T2 · Episódio 0\/24/);

const movie = {
  id: "golden-compass",
  name: "A Bússola de Ouro",
  category: "Filme",
  status: "Concluído",
  current_season: 1,
  current_episode: 1,
  episodes: 1,
  seasons_json: JSON.stringify([{ season: 1, episodes: 1 }]),
};
assert.equal(context.franchiseLabel(movie), "");
assert.equal(context.currentSeasonEpisodeTotal(movie), 0);
assert.equal(context.nextPosition(movie), null);
assert.doesNotMatch(context.card(movie), /📚 T1/);

assert.match(core, /const currentTotal=currentSeasonEpisodeTotal\(x,cs\)/);
assert.match(app, /const currentTotal=currentSeasonEpisodeTotal\(x,currentSeason\)/);
assert.match(app, /field\("Nota pessoal",x\.rating/);
assert.match(app, /String\(x\.notes\|\|""\)\.trim\(\)/);

for (const [filename, source] of [["app.html", app], ["app_core.html", core]]) {
  const scripts = [...source.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script\s*>/gi)]
    .map((match) => match[1])
    .filter((body) => body.trim());
  for (let i = 0; i < scripts.length; i += 1) {
    new vm.Script(scripts[i], { filename: `${filename}:inline-${i + 1}` });
  }
}

console.log("Frontend library regressions: progress, filters, card, season controls, movie badge, and panel details passed.");
