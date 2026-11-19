"""Convert US/imperial quantities in recipe text to rounded metric.

Output keeps the shape Nextcloud Cookbook's servings calculator understands:
"<number> <unit> <ingredient>", with a dot as the decimal separator.
"""

import re
import unicodedata

from .densities import grams_per_cup
from .names import europeanize

# ---------------------------------------------------------------- amounts

_UNI = "[¼-¾⅐-⅞]"
_AMT = (
    r"(?:\d+\s+\d+\s*/\s*\d+"  # 1 1/2
    rf"|\d+\s*{_UNI}"  # 1½
    r"|\d+\s*/\s*\d+"  # 1/2
    rf"|{_UNI}"  # ½
    r"|\d+(?:[.,]\d+)?)"  # 2, 2.5, 2,5
)
# A range joins two amounts with a hyphen, an en dash (written as an escape so
# this file contains none), "to" or "or".
_RANGE_SEP = r"\s*(?:-|\u2013|to|or)\s*"
_START = r"(?<![\w/.,])"


def parse_amount(text):
    """Parse '1 1/2', '1½', '1/2', '½', '2.5' or '2,5' into a float."""
    s = text.strip()
    m = re.fullmatch(r"(\d+)\s+(\d+)\s*/\s*(\d+)", s)
    if m:
        return int(m[1]) + int(m[2]) / int(m[3])
    m = re.fullmatch(rf"(\d+)\s*({_UNI})", s)
    if m:
        return int(m[1]) + unicodedata.numeric(m[2])
    m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", s)
    if m:
        return int(m[1]) / int(m[2])
    if re.fullmatch(_UNI, s):
        return unicodedata.numeric(s)
    return float(s.replace(",", "."))


# ---------------------------------------------------------------- rounding


def _fmt(x):
    """Format a number without trailing zeros: 1.50 -> 1.5, 2.0 -> 2."""
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return s or "0"


def round_metric(x):
    """Round a gram or ml value to a step a cook would actually measure."""
    if x < 10:
        step = 1 if x >= 3 else 0.5
    elif x < 50:
        step = 5
    elif x < 250:
        step = 10
    elif x < 1000:
        step = 25
    else:
        step = 50
    r = round(x / step) * step
    return r if r > 0 else step


def metric_quantity(values, base):
    """Format one value or a range in g or ml, switching to kg or l at 1000."""
    rounded = [round_metric(v) for v in values]
    big = {"g": "kg", "ml": "l"}[base]
    if rounded[0] >= 1000:
        return "-".join(_fmt(r / 1000) for r in rounded) + " " + big
    return "-".join(_fmt(r) for r in rounded) + " " + base


# ---------------------------------------------------------------- units

ML_PER_CUP = 236.588

# (regex, kind, factor). kind "volume" factors are ml, "mass" are g, "spoon"
# are ml for a metric spoon (5 ml teaspoon, 15 ml tablespoon).
_UNITS = [
    (r"fluid\s+ounces?|fl\.?\s*oz", "volume", 29.5735),
    (r"cups?", "cup", ML_PER_CUP),
    (r"tablespoons?|tbsps?|tbls?|tbs", "spoon", 15.0),
    (r"teaspoons?|tsps?", "spoon", 5.0),
    (r"ounces?|oz", "mass", 28.3495),
    (r"pounds?|lbs?", "mass", 453.592),
    (r"pints?", "volume", 473.176),
    (r"quarts?|qts?", "volume", 946.353),
    (r"gallons?", "volume", 3785.41),
    (r"sticks?(?=\s+(?:of\s+)?(?:\w+\s+)?butter)", "mass", 113.0),
]
_UNIT_ALT = "|".join(f"(?:{u[0]})" for u in _UNITS)
_UNIT_COMPILED = [(re.compile(rf"(?:{u[0]})", re.I), u[1], u[2]) for u in _UNITS]
# A trailing abbreviation dot is eaten only when the sentence clearly goes on.
_UNIT_DOT = r"(?:\.(?=\s+[a-z(]))?"

QTY_RE = re.compile(
    rf"{_START}(?P<a>{_AMT})(?:{_RANGE_SEP}(?P<b>{_AMT}))?"
    rf"(?:\s*-\s*|\s*)(?P<unit>{_UNIT_ALT}){_UNIT_DOT}(?![A-Za-z])",
    re.IGNORECASE,
)

_METRIC_UNIT = (
    r"(?:g|grams?|grammes?|kg|kilograms?|ml|millilit(?:er|re)s?|cl|dl|"
    r"l|lit(?:er|re)s?)(?![A-Za-z])"
)
METRIC_QTY = rf"{_START}{_AMT}(?:{_RANGE_SEP}{_AMT})?\s*{_METRIC_UNIT}"
_IMPERIAL_QTY = rf"{_START}{_AMT}(?:{_RANGE_SEP}{_AMT})?(?:\s*-\s*|\s*)(?:{_UNIT_ALT}){_UNIT_DOT}(?![A-Za-z])"
_APPROX = r"(?:~\s*|about\s+|approx\.?\s+|approximately\s+|roughly\s+)?"

# "1 cup (240 ml)" or "8 oz / 225 g": keep the metric value the source gave.
_IMPERIAL_THEN_METRIC = re.compile(
    rf"{_IMPERIAL_QTY}\s*(?:\(\s*{_APPROX}(?P<m1>{METRIC_QTY})\s*\)|/\s*(?P<m2>{METRIC_QTY}))",
    re.IGNORECASE,
)
# "225 g (8 oz)" or "240 ml / 1 cup": drop the imperial echo.
_METRIC_THEN_IMPERIAL = re.compile(
    rf"(?P<m>{METRIC_QTY})\s*(?:\(\s*{_APPROX}{_IMPERIAL_QTY}\s*\)|/\s*{_IMPERIAL_QTY})",
    re.IGNORECASE,
)
# A parenthesis holding nothing but an imperial amount: "(~1 cup)".
_IMPERIAL_ONLY_PAREN = re.compile(rf"\s*\(\s*{_APPROX}{_IMPERIAL_QTY}\s*\)", re.IGNORECASE)


def _unit_info(unit):
    for rx, kind, factor in _UNIT_COMPILED:
        if rx.fullmatch(unit):
            return kind, factor
    raise ValueError(unit)


def _ingredient_after(text, end):
    """The ingredient words right after a quantity: up to '(' or ',' or '.'."""
    rest = text[end:]
    rest = re.sub(r"^\s*(?:of\s+)?", "", rest)
    return re.split(r"[(,.;\d]|\b(?:and|or|then|to)\b", rest, maxsplit=1)[0]


class Options:
    def __init__(self, spoons="keep", names=True):
        # "keep": leave tsp/tbsp alone (metric kitchens use 5/15 ml spoons).
        # "ml": write them as 5 ml / 15 ml.
        self.spoons = spoons
        # Rename US ingredient names to European ones (names.py).
        self.names = names


def _convert_quantities(text, options, context):
    def repl(m):
        kind, factor = _unit_info(m["unit"])
        values = [parse_amount(m["a"])]
        if m["b"]:
            values.append(parse_amount(m["b"]))
        if kind == "spoon":
            if options.spoons == "keep":
                return m[0]
            return "-".join(_fmt(round(v * factor * 2) / 2) for v in values) + " ml"
        if kind == "mass":
            return metric_quantity([v * factor for v in values], "g")
        if kind == "cup":
            name = _ingredient_after(text, m.end())
            if not re.search(r"[A-Za-z]", name):
                name = context
            gpc = grams_per_cup(name)
            if gpc:
                return metric_quantity([v * gpc for v in values], "g")
        return metric_quantity([v * factor for v in values], "ml")

    return QTY_RE.sub(repl, text)


# ---------------------------------------------------------------- temperatures

_DEG = r"(?:\s*[°º˚]\s*|\s+degrees?\s+|\s*)"
_F_TEMP = rf"{_START}(?P<f>\d{{2,3}})(?:{_RANGE_SEP}(?P<f2>\d{{2,3}}))?{_DEG}(?:F\b|Fahrenheit\b)"
_C_TEMP = rf"{_START}\d{{2,3}}(?:{_RANGE_SEP}\d{{2,3}})?{_DEG}(?:C\b|Celsius\b)"
_F_RE = re.compile(_F_TEMP)
_F_THEN_C = re.compile(rf"{_F_TEMP}\s*(?:\(\s*(?P<c>{_C_TEMP})\s*\)|/\s*(?P<c2>{_C_TEMP}))")
_F_TEMP_PLAIN = rf"{_START}\d{{2,3}}(?:{_RANGE_SEP}\d{{2,3}})?{_DEG}(?:F\b|Fahrenheit\b)"
_C_THEN_F = re.compile(rf"(?P<c>{_C_TEMP})\s*(?:\(\s*{_F_TEMP_PLAIN}\s*\)|/\s*{_F_TEMP_PLAIN})")


def _f_to_c(f):
    """Oven temperatures in steps of 10 (400F -> 200C), lower ones in steps of 5."""
    c = (f - 32) * 5 / 9
    step = 10 if c >= 100 else 5
    return int(round(c / step) * step)


def convert_temperatures(text):
    text = _C_THEN_F.sub(lambda m: m["c"], text)
    text = _F_THEN_C.sub(lambda m: m["c"] or m["c2"], text)

    def repl(m):
        out = str(_f_to_c(int(m["f"])))
        if m["f2"]:
            out += "-" + str(_f_to_c(int(m["f2"])))
        return out + "°C"

    return _F_RE.sub(repl, text)


# ---------------------------------------------------------------- lengths

_INCH = re.compile(
    rf"{_START}(?P<a>{_AMT})(?:\s*(?:x|×|by)\s*(?P<b>{_AMT}))?"
    r"(?:\s*-\s*|\s*)(?:inch(?:es)?(?![A-Za-z])|in\.(?=\s)|″|\"(?=\s))"
)


def _cm(inches):
    cm = inches * 2.54
    return _fmt(round(cm * 2) / 2 if cm < 3 else round(cm))


def convert_lengths(text):
    def repl(m):
        out = _cm(parse_amount(m["a"]))
        if m["b"]:
            out += "x" + _cm(parse_amount(m["b"]))
        return out + " cm"

    return _INCH.sub(repl, text)


# ---------------------------------------------------------------- entry points

_DOUBLE_PAREN = re.compile(r"\(\(([^()]*)\)\)")
_STICKS_PAREN = re.compile(rf"\s*\(\s*{_AMT}\s*sticks?\s*\)", re.IGNORECASE)


def convert_text(text, options=None, context=""):
    """Convert all quantities, temperatures and lengths in one piece of text."""
    options = options or Options()
    text = _IMPERIAL_THEN_METRIC.sub(lambda m: m["m1"] or m["m2"], text)
    text = _METRIC_THEN_IMPERIAL.sub(lambda m: m["m"], text)
    text = convert_temperatures(text)
    text = convert_lengths(text)
    text = _convert_quantities(text, options, context)
    if options.names:
        text = europeanize(text)
    return text


def convert_ingredient(line, options=None, tidy=True):
    options = options or Options()
    """Convert one ingredient line.

    With tidy, '((x))' becomes '(x)' (an import artifact), and when the line
    already starts with a metric amount, a parenthesis holding only an
    imperial echo such as '(~1 cup)' is dropped.
    """
    if line.startswith("## "):
        return line
    if tidy:
        line = _DOUBLE_PAREN.sub(r"(\1)", line)
        if re.match(METRIC_QTY, line, re.IGNORECASE):
            line = _IMPERIAL_ONLY_PAREN.sub("", line)
    context = re.sub(r"\([^)]*\)", " ", line)
    line = convert_text(line, options, context=context)
    if tidy and re.match(METRIC_QTY, line, re.IGNORECASE):
        # "230 g (2 sticks) butter": the stick count only echoes the amount.
        line = _STICKS_PAREN.sub("", line)
        line = _IMPERIAL_ONLY_PAREN.sub("", line)
    return line


def _walk_strings(value, fn):
    """Apply fn to every string inside lists and dicts (HowToStep 'text' etc.)."""
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, list):
        return [_walk_strings(v, fn) for v in value]
    if isinstance(value, dict):
        return {
            k: (_walk_strings(v, fn) if k in ("text", "itemListElement", "name") else v)
            for k, v in value.items()
        }
    return value


def convert_recipe(recipe, options=None, tidy=True):
    """Return (new_recipe, changes). changes is a list of (field, old, new)."""
    options = options or Options()
    out = dict(recipe)
    changes = []

    def track(field, fn):
        def wrapped(s):
            new = fn(s)
            if new != s:
                changes.append((field, s, new))
            return new

        return wrapped

    if isinstance(recipe.get("recipeIngredient"), list):
        out["recipeIngredient"] = [
            track("ingredient", lambda s: convert_ingredient(s, options, tidy))(s)
            if isinstance(s, str)
            else s
            for s in recipe["recipeIngredient"]
        ]
    for field, label in (("recipeInstructions", "instruction"), ("tool", "tool")):
        if field in recipe:
            out[field] = _walk_strings(recipe[field], track(label, lambda s: convert_text(s, options)))
    if isinstance(recipe.get("description"), str):
        out["description"] = track("description", lambda s: convert_text(s, options))(recipe["description"])
    return out, changes
