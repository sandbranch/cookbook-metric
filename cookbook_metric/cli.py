"""cookbook-metric: convert Nextcloud Cookbook recipes to rounded metric."""

import argparse
import datetime
import getpass
import json
import os
import sys
import tomllib
from pathlib import Path

from .convert import Options, convert_ingredient, convert_recipe, convert_text
from .nextcloud import (
    PASSWORD_ENV,
    LocalStore,
    RecipeFile,
    RemoteStore,
    lookup_password,
    store_password,
)

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "cookbook-metric" / "config.toml"
BACKUPS = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "cookbook-metric" / "backups"


def load_config():
    if CONFIG.is_file():
        return tomllib.loads(CONFIG.read_text())
    return {}


def open_store(args):
    if args.dir:
        return LocalStore(args.dir)
    cfg = load_config()
    url = args.url or cfg.get("url")
    user = args.user or cfg.get("user")
    if not url or not user:
        sys.exit("Need --url and --user (or run `cookbook-metric login` once), or --dir for a local folder.")
    password = lookup_password(url, user)
    if not password:
        sys.exit(
            f"No app password for {user} at {url}. Run `cookbook-metric login`, "
            f"or set {PASSWORD_ENV}."
        )
    return RemoteStore(url, user, password, folder=args.folder or cfg.get("folder"))


def options_from(args):
    return Options(spoons=args.spoons, names=not args.keep_names, volume=args.volume, solids=args.solids)


def cmd_login(args):
    url = args.url or input("Nextcloud URL (https://...): ").strip()
    user = args.user or input("User name: ").strip()
    print("Create an app password in Nextcloud: Personal settings > Security > Devices & sessions.")
    password = getpass.getpass("App password: ")
    store = RemoteStore(url, user, password)  # checks the login and reads the folder
    store_password(url, user, password)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(f'url = "{url}"\nuser = "{user}"\n')
    print(f"Logged in. Cookbook folder: {store.folder}. Saved {CONFIG} and the keyring entry.")


def cmd_text(args):
    opts = options_from(args)
    for line in args.text:
        print(convert_ingredient(line, opts) if args.ingredient else convert_text(line, opts))


def cmd_convert(args):
    store = open_store(args)
    opts = options_from(args)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = BACKUPS / stamp
    changed = 0
    print(f"Recipes in {store.describe()}")
    for recipe in store.recipes():
        if args.recipe and not any(r.lower() in recipe.folder.lower() for r in args.recipe):
            continue
        new, changes = convert_recipe(recipe.data, opts, tidy=not args.no_tidy)
        if not changes:
            print(f"\n= {recipe.folder}: nothing to convert")
            continue
        changed += 1
        print(f"\n# {recipe.folder}")
        for field, old, new_text in changes:
            print(f"  {field}:\n    - {old}\n    + {new_text}")
        if not args.apply:
            continue
        original = json.dumps(recipe.data, ensure_ascii=False).encode("utf-8")
        target = backup_dir / recipe.folder
        target.mkdir(parents=True, exist_ok=True)
        (target / "recipe.json").write_bytes(original)
        # Cookbook only refreshes its index for a recipe when dateModified changes.
        new["dateModified"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        store.write(recipe, json.dumps(new, ensure_ascii=False).encode("utf-8"))
    print()
    if not args.apply:
        print(f"{changed} recipe(s) would change. Nothing written; add --apply to write.")
        return
    if changed:
        store.reindex()
        print(f"Converted {changed} recipe(s). Originals saved in {backup_dir}")
        print(f"Undo with: cookbook-metric restore {backup_dir}")
    else:
        print("Nothing to convert.")


def cmd_restore(args):
    store = open_store(args)
    src = Path(args.backup)
    current = {r.folder: r for r in store.recipes()}
    for d in sorted(p for p in src.iterdir() if p.is_dir()):
        raw = (d / "recipe.json").read_bytes()
        data = json.loads(raw)
        # Bump dateModified so Cookbook reindexes the restored recipe too.
        data["dateModified"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        target = current.get(d.name, RecipeFile(d.name, None))
        store.write(target, json.dumps(data, ensure_ascii=False).encode("utf-8"))
        print(f"Restored {d.name}")
    store.reindex()


def main(argv=None):
    p = argparse.ArgumentParser(prog="cookbook-metric", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def where(sp):
        sp.add_argument("--url", help="Nextcloud URL, e.g. https://next.example.com")
        sp.add_argument("--user", help="Nextcloud user name")
        sp.add_argument("--folder", help="Cookbook folder (default: read from Cookbook's settings)")
        sp.add_argument("--dir", help="work on a local folder of recipe folders instead of a server")

    def conversion(sp):
        sp.add_argument("--spoons", choices=["keep", "ml"], default="keep",
                        help="keep tsp/tbsp as they are (default) or write them as 5/15 ml")
        sp.add_argument("--volume", choices=["dl", "ml"], default="dl",
                        help="volume unit for cups and liquids (default dl)")
        sp.add_argument("--solids", choices=["volume-first", "grams-first", "grams"], default="volume-first",
                        help="flour, sugar etc.: '5.5 dl (275 g)' (default), '275 g (5.5 dl)' or grams only")
        sp.add_argument("--keep-names", action="store_true",
                        help="do not rename US ingredient names (all-purpose flour -> wheat flour)")

    sp = sub.add_parser("login", help="store server, user and app password")
    sp.add_argument("--url")
    sp.add_argument("--user")
    sp.set_defaults(fn=cmd_login)

    sp = sub.add_parser("convert", help="convert recipes (dry run unless --apply)")
    where(sp)
    conversion(sp)
    sp.add_argument("--apply", action="store_true", help="write the changes (originals are backed up first)")
    sp.add_argument("--recipe", action="append", help="only recipes whose folder name contains this (repeatable)")
    sp.add_argument("--no-tidy", action="store_true",
                    help="keep '((x))' and imperial echoes like '(~1 cup)' after a metric amount")
    sp.set_defaults(fn=cmd_convert)

    sp = sub.add_parser("restore", help="put back the originals from a backup folder")
    where(sp)
    sp.add_argument("backup")
    sp.set_defaults(fn=cmd_restore)

    sp = sub.add_parser("text", help="convert text given on the command line, for trying things out")
    conversion(sp)
    sp.add_argument("--ingredient", action="store_true", help="treat each argument as an ingredient line")
    sp.add_argument("text", nargs="+")
    sp.set_defaults(fn=cmd_text)

    args = p.parse_args(argv)
    args.fn(args)
