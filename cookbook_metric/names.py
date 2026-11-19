"""Rename US ingredient and kitchen names to what a Nordic kitchen calls them.

The recipe stays in English; only US-specific names change, so the result
reads like a recipe written in northern Europe: "all-purpose flour" ->
"wheat flour". The list lives in names.toml next to this file, and the user
can extend or override it in ~/.config/cookbook-metric/names.toml.
"""

import os
import re
import tomllib
from importlib import resources
from pathlib import Path

USER_NAMES = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "cookbook-metric" / "names.toml"


def _key(phrase):
    return re.sub(r"[\s-]+", " ", phrase.strip().lower())


def _phrase_pattern(phrase):
    # Space and hyphen are interchangeable; any run of them matches.
    return r"[\s-]+".join(re.escape(p) for p in _key(phrase).split(" "))


def load_table(user_file=USER_NAMES):
    """{old phrase (normalised): new name}, built-in first, user file on top."""
    table = {}
    sources = [resources.files(__package__).joinpath("names.toml").read_text(encoding="utf-8")]
    if user_file and Path(user_file).is_file():
        sources.append(Path(user_file).read_text(encoding="utf-8"))
    for text in sources:
        for new, olds in tomllib.loads(text).items():
            for old in olds:
                table[_key(old)] = new
    return table


class Renamer:
    def __init__(self, user_file=USER_NAMES):
        self.table = load_table(user_file)
        # Longest phrase first, so "bell peppers" wins over "bell pepper".
        olds = sorted(self.table, key=len, reverse=True)
        self.rx = re.compile(
            r"(?<![\w-])(?:" + "|".join(_phrase_pattern(o) for o in olds) + r")(?![\w-])",
            re.IGNORECASE,
        )

    def __call__(self, text):
        def repl(m):
            new = self.table[_key(m[0])]
            if m[0][:1].isupper():
                new = new[:1].upper() + new[1:]
            return new

        return self.rx.sub(repl, text)


_default = None


def europeanize(text):
    """Rename US names in text using the built-in plus the user's list."""
    global _default
    if _default is None:
        _default = Renamer()
    return _default(text)
