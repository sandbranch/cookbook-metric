import json
import shutil
import tempfile
import unittest
from pathlib import Path

from cookbook_metric import cli
from cookbook_metric.convert import Options, convert_ingredient, convert_recipe, convert_text, parse_amount, round_metric
from cookbook_metric.names import Renamer, load_table

FIXTURES = Path(__file__).parent / "fixtures"
NO_USER = Options()


class Amounts(unittest.TestCase):
    def test_parse(self):
        for text, value in [("1 1/2", 1.5), ("1½", 1.5), ("1/2", 0.5), ("½", 0.5), ("2.5", 2.5), ("2,5", 2.5), ("3", 3)]:
            self.assertAlmostEqual(parse_amount(text), value, msg=text)

    def test_rounding(self):
        self.assertEqual(round_metric(118.3), 120)
        self.assertEqual(round_metric(236.6), 240)
        self.assertEqual(round_metric(354.9), 350)
        self.assertEqual(round_metric(28.35), 30)
        self.assertEqual(round_metric(4.4), 4)
        self.assertEqual(round_metric(1.2), 1)


class Ingredients(unittest.TestCase):
    cases = [
        # Okonomiyaki as imported from norecipes.com
        ("400 grams cabbage ((~half a small head))", "400 grams cabbage (~half a small head)"),
        ("60 grams scallions ((~2 , thinly sliced))", "60 grams spring onions (~2 , thinly sliced)"),
        ("140 grams all-purpose flour ((~1 cup))", "140 grams wheat flour"),
        ("1/2 cup dashi stock ((or cold water))", "120 ml dashi stock (or cold water)"),
        # Cups of solids are weighed, cups of liquids measured
        ("2 1/4 cups all-purpose flour", "275 g wheat flour"),
        ("1 cup packed brown sugar", "210 g packed brown sugar"),
        ("3/4 cup powdered sugar", "80 g icing sugar"),
        ("1 1/2 cups milk", "350 ml milk"),
        ("1 cup (2 sticks) unsalted butter, softened", "230 g butter, softened"),
        # The source's own metric value is kept
        ("1 cup (240 ml) heavy cream", "240 ml whipping cream"),
        ("225 g (8 oz) cream cheese", "225 g cream cheese"),
        # Mass, ranges, parenthesised sizes
        ("8 oz cream cheese", "230 g cream cheese"),
        ("2-3 lbs chicken thighs", "900-1350 g chicken thighs"),
        ("1 (14-ounce) can coconut milk", "1 (400 g) can coconut milk"),
        ("3 lb pork shoulder", "1.35 kg pork shoulder"),
        # Spoons stay by default, things without units stay
        ("1 tsp baking soda", "1 tsp bicarbonate of soda"),
        ("2 large eggs", "2 large eggs"),
        ("9 slices bacon", "9 slices bacon"),
        ("okonomiyaki sauce", "okonomiyaki sauce"),
        ("## For the sauce", "## For the sauce"),
        # Names
        ("1 red bell pepper", "1 red paprika"),
        ("2 green bell peppers, diced", "2 green paprikas, diced"),
        ("1 tsp paprika", "1 tsp paprika"),
        ("2 courgettes", "2 zucchini"),
        ("1 summer squash", "1 zucchini"),
    ]

    def test_cases(self):
        for before, after in self.cases:
            self.assertEqual(convert_ingredient(before, NO_USER), after, msg=before)

    def test_idempotent(self):
        for before, _ in self.cases:
            once = convert_ingredient(before, NO_USER)
            self.assertEqual(convert_ingredient(once, NO_USER), once, msg=before)

    def test_spoons_ml(self):
        opts = Options(spoons="ml")
        self.assertEqual(convert_ingredient("1/2 tsp salt", opts), "2.5 ml salt")
        self.assertEqual(convert_ingredient("2 tbsp olive oil", opts), "30 ml olive oil")

    def test_keep_names(self):
        self.assertEqual(convert_ingredient("1 cup all-purpose flour", Options(names=False)), "120 g all-purpose flour")


class Instructions(unittest.TestCase):
    cases = [
        ("Preheat oven to 350°F (175°C).", "Preheat oven to 175°C."),
        ("Bake at 180°C / 350°F", "Bake at 180°C"),
        ("Bake at 425 degrees F for 20 minutes.", "Bake at 220°C for 20 minutes."),
        ("Heat oven to 400F.", "Heat oven to 200°C."),
        ("Bake at 375°F.", "Bake at 190°C."),
        ("Let rise at 80°F.", "Let rise at 25°C."),
        ("Grease a 9x13-inch pan.", "Grease a 23x33 cm pan."),
        ("Line a baking sheet with parchment paper.", "Line a baking tray with baking paper."),
        ("Add 1 cup flour and 2 cups water.", "Add 120 g flour and 475 ml water."),
        ("Add 1 oz. cheese and stir.", "Add 30 g cheese and stir."),
        ("Bake for 20 minutes.", "Bake for 20 minutes."),
    ]

    def test_cases(self):
        for before, after in self.cases:
            self.assertEqual(convert_text(before, NO_USER), after, msg=before)


class Names(unittest.TestCase):
    def test_new_names_are_stable(self):
        """No new name is itself renamed again (running twice changes nothing)."""
        r = Renamer(user_file=None)
        for new in set(load_table(user_file=None).values()):
            self.assertEqual(r(new), new, msg=new)

    def test_case_and_hyphens(self):
        r = Renamer(user_file=None)
        self.assertEqual(r("All purpose flour"), "Wheat flour")
        self.assertEqual(r("semi-sweet chocolate chips"), "dark chocolate chips")
        self.assertEqual(r("ground cinnamon"), "ground cinnamon")

    def test_user_override(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "names.toml"
            f.write_text('"shrimp" = ["shrimp"]\n"ruccola" = ["arugula", "rocket"]\n')
            r = Renamer(user_file=f)
            self.assertEqual(r("shrimp and arugula"), "shrimp and ruccola")


class Recipe(unittest.TestCase):
    def test_fixture(self):
        recipe = json.loads((FIXTURES / "Okonomiyaki" / "recipe.json").read_text())
        new, changes = convert_recipe(recipe, NO_USER)
        self.assertIn("120 ml dashi stock (or cold water)", new["recipeIngredient"])
        self.assertIn("Bake at 200°C until crisp.", new["recipeInstructions"])
        self.assertEqual(new["tool"], ["baking tray", "23 cm frying pan"])
        self.assertTrue(changes)
        again, more = convert_recipe(new, NO_USER)
        self.assertEqual(more, [])

    def test_cli_dry_run_and_apply(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copytree(FIXTURES, d, dirs_exist_ok=True)
            f = Path(d) / "Okonomiyaki" / "recipe.json"
            before = f.read_text()
            cli.BACKUPS = Path(d) / "backups"
            cli.main(["convert", "--dir", d])
            self.assertEqual(f.read_text(), before)
            cli.main(["convert", "--dir", d, "--apply"])
            after = json.loads(f.read_text())
            self.assertIn("120 ml dashi stock (or cold water)", after["recipeIngredient"])
            self.assertNotEqual(after["dateModified"], json.loads(before)["dateModified"])
            backup = next((Path(d) / "backups").iterdir())
            cli.main(["restore", "--dir", d, str(backup)])
            self.assertEqual(json.loads(f.read_text())["recipeIngredient"], json.loads(before)["recipeIngredient"])


if __name__ == "__main__":
    unittest.main()
