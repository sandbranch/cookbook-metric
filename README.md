# cookbook-metric

Converts the recipes stored in [Nextcloud Cookbook](https://github.com/nextcloud/cookbook)
to rounded metric units and Nordic/European ingredient names, in place.

```
1/2 cup dashi stock ((or cold water))     ->  120 ml dashi stock (or cold water)
2 1/4 cups all-purpose flour              ->  275 g wheat flour
1 cup (2 sticks) unsalted butter          ->  230 g butter
140 grams all-purpose flour ((~1 cup))    ->  140 grams wheat flour
2 green bell peppers, diced               ->  2 green paprikas, diced
Preheat oven to 350°F (175°C).            ->  Preheat oven to 175°C.
Grease a 9x13-inch pan.                   ->  Grease a 23x33 cm pan.
Line a baking sheet with parchment paper. ->  Line a baking tray with baking paper.
```

Cookbook has no unit conversion of its own (feature request
[nextcloud/cookbook#2517](https://github.com/nextcloud/cookbook/issues/2517)).
Each recipe is a schema.org `recipe.json` file in your Nextcloud files, so this
tool rewrites those files over WebDAV and asks Cookbook to reindex.

## What it converts

- **Cups of solids to grams**, using a density table from King Arthur Baking's
  [ingredient weight chart](https://www.kingarthurbaking.com/learn/ingredient-weight-chart)
  (flour, sugars, butter, oats, nuts, cheese, ...). Cups of liquids, and
  anything not in the table, become ml.
- **oz, lb to g; fl oz, pints, quarts, gallons to ml.** Values are rounded to
  what you would measure: steps of 5 below 50, 10 below 250, 25 below 1000,
  then kg/l.
- **°F to °C**, ovens in steps of 10 (350°F becomes 180°C). Where the recipe
  already gives °C next to °F, that value is kept and the °F dropped.
- **Inches to cm**, including pan sizes such as 9x13-inch.
- **Teaspoons and tablespoons stay as they are** by default: metric kitchens
  use 5 ml and 15 ml spoons. `--spoons ml` writes them as ml.
- **US names to Nordic/European ones**: all-purpose flour to wheat flour,
  powdered sugar to icing sugar, scallions to spring onions, bell pepper to
  paprika, baking soda to bicarbonate of soda, parchment paper to baking paper,
  and so on. The list is [`cookbook_metric/names.toml`](cookbook_metric/names.toml).
  Add your own or override entries in `~/.config/cookbook-metric/names.toml`
  (same format, yours win). `--keep-names` turns renaming off.
- **Tidying**: `((x))` from the importer becomes `(x)`, and an imperial echo
  such as `(~1 cup)` after an amount that is already metric is dropped.
  `--no-tidy` turns this off.

The result keeps the `amount unit ingredient` shape, so Cookbook's servings
calculator still works on it. Running the tool twice changes nothing the
second time.

## Use

Needs Python 3.11 or newer and nothing else.

```sh
pipx install git+https://github.com/sandbranch/cookbook-metric
cookbook-metric login          # server URL, user name, app password (stored in the keyring)
cookbook-metric convert        # dry run: shows every change, writes nothing
cookbook-metric convert --apply
```

Create the app password in Nextcloud under Personal settings > Security >
Devices & sessions. The password can also come from `COOKBOOK_METRIC_PASSWORD`.

`--apply` saves every original `recipe.json` under
`~/.local/share/cookbook-metric/backups/<time>/` before writing, and writes
with `If-Match`, so a recipe edited in the meantime is not overwritten. To undo:

```sh
cookbook-metric restore ~/.local/share/cookbook-metric/backups/<time>
```

Other options: `--recipe NAME` (only recipes whose folder name contains NAME),
`--dir PATH` (a local copy of the recipe folder instead of a server), and
`cookbook-metric text "1 cup flour"` to try a conversion without touching
anything.

## Development

```sh
python3 -m unittest discover -s tests       # unit tests, no network
dev/local-nextcloud.sh up                   # throwaway Nextcloud 33 + Cookbook in Docker
dev/local-nextcloud.sh e2e                  # upload a recipe, convert, check via the API, restore
dev/local-nextcloud.sh down
```

## Licence

AGPL-3.0-or-later, the same as Nextcloud Cookbook, so the conversion logic can
move into the app as an import filter later.
