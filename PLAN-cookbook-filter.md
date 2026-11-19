# Plan: metric conversion inside Nextcloud Cookbook

Written 2026-11-19. Not started; this is the "proper route" to do later.

## Why

`cookbook-metric` (this repo) converts recipes that are already stored. New
imports still arrive in cups and °F until the tool runs again. The fix is to
convert inside Cookbook itself, at the moment a recipe is saved, so nothing
needs to run on the side.

## What we checked in Cookbook (main at 20daabd, app 0.11.12)

- Every save goes through one filter chain. `RecipeService::addRecipe()`
  calls `checkRecipe()`, which runs `JSONFilter::apply()`
  (`lib/Helper/Filter/JSON/JSONFilter.php`). `downloadRecipe()` (URL import)
  also calls `checkRecipe()` and then `addRecipe()`. So imports, manual
  creates and manual edits all pass the filters.
- Reading a recipe does not run the filters: `parseRecipeFile()` only
  decodes the JSON. So a filter changes what is stored, not what is shown.
- The filters are plain classes, wired in by constructor injection into
  `JSONFilter`; each extends `AbstractJSONFilter` and has
  `apply(array &$json): bool`. Tests for each sit in
  `tests/Unit/Helper/Filter/JSON/<Name>Test.php`.
- Per-user settings live in `lib/Helper/UserConfigHelper.php` (keys such as
  `print_image`, `visible_info_blocks`), exposed by
  `lib/Controller/Implementation/ConfigImplementation.php` (GET and POST
  `/api/v1/config`) and shown in `src/components/Modals/SettingsDialog.vue`.
- The API is documented in `docs/dev/api/trunk/openapi-cookbook.yaml`; the PR
  template asks for an OpenAPI update and an API changelog entry when the
  API changes, and a note in the Matrix channel.
- The servings calculator (`src/js/yieldCalculator.js`) scales only the
  first amount on a line, and does not accept ranges ("2-3 cups").
- No `occ` commands exist in the app yet (no `lib/Command`).
- Requirements: PHP 8.0+, Nextcloud 31 to 36. Frontend tests are Vitest.
- Feature request: nextcloud/cookbook#2517 (open since 2025-07, no
  maintainer reply yet).

## Contribution rules that apply (from Cookbook's AGENTS.md)

These are the project's rules for AI-assisted work, so the plan follows them:

- Every commit with AI-assisted content carries an
  `Assisted-by: Claude:claude-opus-5-5` trailer. Commits are signed and signed
  off (`git commit -s -S`), and only David signs off (DCO); Claude never adds
  `Signed-off-by`.
- Conventional commit messages, no `cookbook` scope.
- PR descriptions and issue comments are written by David in his own words,
  with a disclosure of AI use. Claude can prepare notes, not the text.
- Claude does not open issues or PRs, or post comments; David does.
- One concern per PR; warn if a PR grows towards thousands of lines.
- For something this size, discuss on a ticket first: comment on #2517
  before writing the PHP.

## Shape of the work: three small PRs and a fork

Upstream will want small, focused PRs, and they may not want the Nordic
name table at all. So split the work into independent pieces:

| # | Piece | Where | Size |
|---|---|---|---|
| 1 | Servings calculator scales amounts in parentheses too | upstream PR | small, frontend |
| 2 | Unit conversion filter + settings | upstream PR | medium, PHP + settings UI |
| 3 | Regional ingredient names (US to Nordic) | fork first, upstream if wanted | small once 2 exists |
| 4 | Convert existing recipes (`occ` command) | upstream PR, after 2 | small |

Until they are merged, run them from a fork, `sandbranch/cookbook`, the same
way as the News Roomy mode (fork, a combined branch on the release tag
installed on the server).

### Step 0: talk first (David)

Comment on #2517 with the proposal: an opt-in, per-user setting that converts
US units at save time (store metric), plus the servings fix. Ask whether they
prefer conversion when saving (what we have) or only for display. Wait for a
direction before PR 2. PR 1 can go ahead regardless; it is a plain bug fix.

### PR 1: scale amounts inside parentheses

`5 dl (140 g) wheat flour` at double servings shows `10 dl (140 g)` today; the
grams should double too.

- In `recalculateIngredients()`, after scaling the leading amount, scale each
  further `number unit` inside parentheses on the same line, where the
  unit is a known measure (g, kg, ml, cl, dl, l, oz, lb, cup, tbsp, tsp).
  Leave counts such as `(~2, thinly sliced)` alone.
- Optionally accept a range at the start (`900-1350 g`) and scale both ends.
- Tests: extend the existing Vitest suite for `yieldCalculator`.
- Useful on its own for anyone whose recipes say `1 cup (240 ml)`.

### PR 2: `MetricConversionFilter` and its settings

Backend:

- `lib/Helper/Filter/JSON/MetricConversionFilter.php`, extends
  `AbstractJSONFilter`, injects `UserConfigHelper`. Registered in
  `JSONFilter` after `FixIngredientsFilter` and `FixInstructionsFilter`, so it
  sees the cleaned-up lists. Returns early when the setting is off.
- Works on `recipeIngredient`, `recipeInstructions`, `tool`, `description`.
- Port of `cookbook_metric/convert.py`:
  - amounts: whole, decimal with `.` or `,`, `1/2`, `1 1/2`, unicode
    fractions (a fixed map instead of Python's `unicodedata`), ranges
  - units table, cups to g by density for dry goods, to dl/ml otherwise
  - rounding rules (`round_metric`, dl steps), °F to °C in steps of 10 for
    ovens, inches to cm
  - kept metric values from the source (`1 cup (240 ml)`), dropped echoes
    (`(~1 cup)`), `((x))` tidy, sticks of butter
  - "volume first" output `5.5 dl (275 g) wheat flour`
- Density table: `lib/Helper/Filter/JSON/Metric/Densities.php` as a constant
  array (pattern, grams per cup, measured by volume). Keep the King Arthur
  source comment.
- PCRE notes: use the `u` flag everywhere; named groups work as in Python;
  write the en dash as `\x{2013}` in patterns.
- Idempotent: running the filter on its own output changes nothing. This
  matters because every edit re-runs it.

Settings (`UserConfigHelper`, config API, `SettingsDialog.vue`):

| Key | Values | Default |
|---|---|---|
| `metric_conversion` | `off`, `on` | `off` (opt-in, nothing changes for existing users) |
| `metric_volume_unit` | `ml`, `dl` | `ml` upstream; we set `dl` |
| `metric_dry_goods` | `grams`, `volume_first`, `grams_first` | `grams` upstream; we set `volume_first` |
| `metric_spoons` | `keep`, `ml` | `keep` |

- Add getters/setters with tests to `UserConfigHelper` and its test.
- Add the keys to `ConfigImplementation::list()` and `config()`.
- Update `docs/dev/api/trunk/openapi-cookbook.yaml` and the API changelog;
  David posts the API change in the Matrix channel, as the PR template asks.
- `SettingsDialog.vue`: a "Units" section with the four settings; strings
  through `t('cookbook', ...)` so they get translated.

Tests:

- PHPUnit `tests/Unit/Helper/Filter/JSON/MetricConversionFilterTest.php`
  with data providers.
- Shared cases: move the cases from `tests/test_convert.py` in this repo into
  `tests/cases.json` here first, use them in both the Python tests and the
  PHPUnit data provider. Then both implementations are tested against the
  same list, and the Python tool stays a reference.
- A case per setting combination, plus "runs twice, same result".

### Piece 3: regional names

- The name table (`cookbook_metric/names.toml`) is an opinion: Nordic
  English. Upstream may not want it, so build it as a separate filter
  (`IngredientNamesFilter`) with its own setting
  (`ingredient_names`: `off`, `nordic`) and the table as a JSON or PHP data
  file. Running it after the unit filter keeps the density lookup on the
  original names, as the Python tool does.
- Keep it on the fork; offer it upstream only if the #2517 discussion shows
  interest. A cleaner upstream version would be per-locale tables
  (en-US to en-GB, and so on).
- The Swedish list from `data/names-sv.tsv` needs review first (see the
  issues listed in README "Name candidates") before any translate mode.

### PR 4: convert what is already stored

- `occ cookbook:convert-units <user> [--dry-run]`: walks the user's recipe
  folder, runs the same filter, prints the changes, writes with a fresh
  `dateModified` and reindexes. New `lib/Command/ConvertUnits.php` and a
  `<commands>` entry in `appinfo/info.xml`.
- Maybe also a "Convert my existing recipes" button in the settings dialog
  calling a new API endpoint; decide after the maintainers' feedback.
- Until then, the Python tool does this job (`cookbook-metric convert`).

## Fork and deployment (until merged)

- Fork `nextcloud/cookbook` to `sandbranch/cookbook`; remote `upstream`.
- One branch per PR off upstream `master`; a combined branch on the tag the
  server runs (now 0.11.12), e.g. `metric-0.11.12`.
- Build the release tarball (`make` / `npm run build`, see
  `DEVELOPMENT.md`), drop `signature.json` so Nextcloud skips the integrity
  check, install the same way as News Roomy on CT 102 (container `nextcloud`,
  app code in volume `nextcloud_html`). Keep the stock app as a backup.
- Test locally first with `dev/local-nextcloud.sh` from this repo, with the
  fork mounted as `custom_apps/cookbook` instead of the store app.

## Order and rough effort

1. Comment on #2517 (David), and extract `tests/cases.json` here. 1 hour.
2. PR 1, servings in parentheses. Half a day.
3. PR 2, filter + settings + tests. 2 to 3 days.
4. Fork branch with 1 + 2 + names filter, install on the server. Half a day.
5. PR 4, `occ` command. Half a day.
6. Names upstream, if wanted.

## Open questions for the maintainers

- Convert on save (stored metric) or only on display?
- Is a per-user setting the right place, or per recipe?
- Would they take regional name tables, and in what form?
