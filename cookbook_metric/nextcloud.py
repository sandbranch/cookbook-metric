"""Read and write Nextcloud Cookbook recipe.json files.

Two stores with the same interface:

- RemoteStore: the user's Nextcloud over WebDAV, plus the Cookbook API for
  the recipe folder setting and the reindex call.
- LocalStore: a folder on disk laid out like the Cookbook folder
  (<folder>/<recipe name>/recipe.json), for tests and synced copies.
"""

import base64
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

PASSWORD_ENV = "COOKBOOK_METRIC_PASSWORD"


def lookup_password(url, user):
    """App password from the environment, else from the desktop keyring."""
    if os.environ.get(PASSWORD_ENV):
        return os.environ[PASSWORD_ENV]
    if shutil.which("secret-tool"):
        r = subprocess.run(
            ["secret-tool", "lookup", "service", "cookbook-metric", "server", url, "user", user],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


def store_password(url, user, password):
    subprocess.run(
        ["secret-tool", "store", "--label", f"cookbook-metric {user}@{url}",
         "service", "cookbook-metric", "server", url, "user", user],
        input=password,
        text=True,
        check=True,
    )


class RecipeFile:
    def __init__(self, folder, data, etag=None):
        self.folder = folder  # recipe folder name, e.g. "Okonomiyaki"
        self.data = data
        self.etag = etag


class LocalStore:
    def __init__(self, root):
        self.root = Path(root)

    def describe(self):
        return str(self.root)

    def recipes(self):
        for d in sorted(p for p in self.root.iterdir() if p.is_dir()):
            f = d / "recipe.json"
            if f.is_file():
                yield RecipeFile(d.name, json.loads(f.read_text(encoding="utf-8")))

    def write(self, recipe, raw):
        (self.root / recipe.folder / "recipe.json").write_bytes(raw)

    def reindex(self):
        pass


class HTTPError(Exception):
    pass


class RemoteStore:
    def __init__(self, url, user, password, folder=None):
        self.url = url.rstrip("/")
        self.user = user
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.auth = f"Basic {token}"
        self.folder = folder or self.cookbook_folder()

    def describe(self):
        return f"{self.url} ({self.user}), folder {self.folder}"

    def _request(self, method, url, body=None, headers=None):
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", self.auth)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            raise HTTPError(f"{method} {url}: HTTP {e.code} {e.reason}") from None

    def _api(self, method, path):
        url = f"{self.url}/index.php/apps/cookbook/api/v1/{path}"
        _, _, body = self._request(method, url, headers={"OCS-APIRequest": "true", "Accept": "application/json"})
        return json.loads(body) if body.strip() else None

    def cookbook_folder(self):
        return self._api("GET", "config")["folder"]

    def reindex(self):
        self._api("POST", "reindex")

    def _dav(self, *parts):
        path = "/".join(urllib.parse.quote(p.strip("/")) for p in (self.folder, *parts) if p.strip("/"))
        return f"{self.url}/remote.php/dav/files/{urllib.parse.quote(self.user)}/{path}"

    def recipes(self):
        body = (
            b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
            b"<d:resourcetype/></d:prop></d:propfind>"
        )
        _, _, xml = self._request("PROPFIND", self._dav() + "/", body, {"Depth": "1", "Content-Type": "application/xml"})
        ns = {"d": "DAV:"}
        base = urllib.parse.urlparse(self._dav() + "/").path.rstrip("/")
        folders = []
        for resp in ET.fromstring(xml).findall("d:response", ns):
            href = urllib.parse.unquote(resp.find("d:href", ns).text).rstrip("/")
            if resp.find(".//d:collection", ns) is None or href == urllib.parse.unquote(base):
                continue
            folders.append(href.rsplit("/", 1)[1])
        for name in sorted(folders):
            try:
                _, headers, raw = self._request("GET", self._dav(name, "recipe.json"))
            except HTTPError as e:
                if "HTTP 404" in str(e):
                    continue
                raise
            etag = {k.lower(): v for k, v in headers.items()}.get("etag")
            yield RecipeFile(name, json.loads(raw), etag)

    def write(self, recipe, raw):
        headers = {"Content-Type": "application/json"}
        if recipe.etag:
            # Refuse to overwrite if the recipe was edited since we read it.
            headers["If-Match"] = recipe.etag
        self._request("PUT", self._dav(recipe.folder, "recipe.json"), raw, headers)
