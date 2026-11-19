"""Grams per US cup for ingredients that metric recipes weigh instead of measure.

Values are from King Arthur Baking's ingredient weight chart
(https://www.kingarthurbaking.com/learn/ingredient-weight-chart), checked
2026-11-19. Where the chart lists a smaller volume (1/2 cup, 1 tbsp) the value
here is scaled up to one cup.

Anything not matched here is converted by volume (ml). Liquids are left out on
purpose: metric recipes measure milk, water, oil, cream and so on in ml/dl.

Order matters: the first pattern that matches the ingredient text wins, so
specific names ("brown sugar", "almond flour") come before general ones
("sugar", "flour").
"""

import re

_TABLE = [
    # Flours and starches
    (r"almond (flour|meal)", 96),
    (r"rice flour", 142),
    (r"rye flour", 106),
    (r"(whole[ -]?wheat|wholemeal|graham) flour", 113),
    (r"spelt flour", 99),
    (r"buckwheat flour", 120),
    (r"semolina", 163),
    (r"potato (starch|flour)", 152),
    (r"corn ?starch|cornflour", 112),
    (r"corn ?meal|polenta", 138),
    (r"\bflour\b", 120),  # all-purpose, bread, cake, plain, self-raising
    # Sugars
    (r"(brown|muscovado|demerara) sugar", 213),
    (r"(powdered|confectioners'?|icing) sugar", 113),
    (r"\bsugar\b", 198),
    # Fats and spreads (butter and cream cheese are weighed, not measured)
    (r"peanut butter", 270),
    (r"\bbutter\b", 226, False),
    (r"cream cheese", 227, False),
    (r"mayo(nnaise)?", 226),
    # Sweet and sticky
    (r"\bhoney\b", 336),
    (r"molasses|treacle", 340),
    (r"chocolate chips?|chocolate chunks?", 170),
    (r"cocoa", 84),
    # Grains and dry goods
    (r"panko", 50),
    (r"bread ?crumbs?", 112),
    (r"(rolled|old[ -]fashioned|quick) oats|\boats\b", 89),
    (r"\brice\b", 198),
    (r"raisins?|sultanas?", 149),
    (r"(shredded|desiccated|flaked) coconut|\bcoconut flakes\b", 85),
    (r"walnuts?", 113),
    (r"pecans?", 114),
    (r"almonds?", 86),
    (r"(powdered|dried|dry) milk|milk powder", 100),
    # Cheese
    (r"parmesan|parmigiano|pecorino", 100),
    (r"(cheddar|mozzarella|jack|swiss|gruy[eè]re|emmental|cheese)", 113),
    # Salt and leavening, for when a recipe measures them in cups
    (r"baking soda|bicarbonate", 288, False),
    (r"kosher salt|coarse salt", 128, False),
    (r"\bsalt\b", 288, False),
]

# A third element False means Nordic kitchens weigh it rather than measure
# it in dl, so it gets grams only. Everything else gets both.
_COMPILED = [(re.compile(e[0], re.IGNORECASE), e[1], e[2] if len(e) > 2 else True) for e in _TABLE]

# Ingredient words that mean "measure this by volume" even if a solid
# also appears in the text, e.g. "milk (or butter milk)", "coconut milk".
_LIQUID = re.compile(
    r"\b(water|milk|cream|stock|broth|dashi|juice|oil|vinegar|wine|beer|"
    r"sauce|syrup|buttermilk|yogh?urt|kefir|sour cream|cr[eè]me fra[iî]che)\b",
    re.IGNORECASE,
)


def grams_per_cup(text):
    """Return grams per US cup for the ingredient named in text, or None.

    None means: convert by volume. That is the answer for liquids and for
    anything the table does not know.
    """
    d = density(text)
    return d[0] if d else None


def density(text):
    """Return (grams per US cup, measured by volume in Nordic kitchens) or None."""
    if not text:
        return None
    if _LIQUID.search(text) and not re.search(
        r"cream cheese|peanut butter|\bbutter\b(?!milk)|milk powder|powdered milk|dried milk",
        text,
        re.IGNORECASE,
    ):
        return None
    for pattern, grams, by_volume in _COMPILED:
        if pattern.search(text):
            return grams, by_volume
    return None
