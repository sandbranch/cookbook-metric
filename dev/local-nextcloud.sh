#!/bin/sh
# Throwaway local Nextcloud with the Cookbook app, for testing cookbook-metric
# against a real server (WebDAV, the Cookbook config and reindex API).
#
#   dev/local-nextcloud.sh up     start at http://127.0.0.1:8089 (admin/admin)
#   dev/local-nextcloud.sh e2e    upload the test recipe, convert it, check, restore
#   dev/local-nextcloud.sh down   remove the container
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
NAME=cookbook-metric-dev
IMAGE=${NEXTCLOUD_IMAGE:-nextcloud:33-apache}
PORT=${PORT:-8089}
URL=http://127.0.0.1:$PORT

occ() {
	docker exec -u www-data "$NAME" php occ "$@"
}

case "${1:-up}" in
up)
	docker rm -f "$NAME" >/dev/null 2>&1 || true
	docker run -d --name "$NAME" -p "127.0.0.1:$PORT:80" \
		-e SQLITE_DATABASE=nextcloud \
		-e NEXTCLOUD_ADMIN_USER=admin -e NEXTCLOUD_ADMIN_PASSWORD=admin \
		-e NEXTCLOUD_TRUSTED_DOMAINS="127.0.0.1 localhost" \
		"$IMAGE" >/dev/null
	echo "Waiting for Nextcloud to install..."
	until occ status 2>/dev/null | grep -q "installed: true"; do sleep 3; done
	occ app:install cookbook
	echo "Nextcloud with Cookbook is up at $URL (admin/admin)"
	;;
e2e)
	export COOKBOOK_METRIC_PASSWORD=admin
	export XDG_DATA_HOME
	XDG_DATA_HOME=$(mktemp -d)
	cd "$REPO"
	run() { python3 -m cookbook_metric "$@" --url "$URL" --user admin; }
	# Reading the config makes Cookbook create its folder.
	folder=$(curl -sf -u admin:admin -H "OCS-APIRequest: true" "$URL/index.php/apps/cookbook/api/v1/config" |
		python3 -c 'import json,sys; print(json.load(sys.stdin)["folder"])')
	dav="$URL/remote.php/dav/files/admin$folder"
	curl -sf -u admin:admin -X MKCOL "$dav" >/dev/null || true
	curl -sf -u admin:admin -X MKCOL "$dav/Okonomiyaki" >/dev/null || true
	curl -sf -u admin:admin -T tests/fixtures/Okonomiyaki/recipe.json "$dav/Okonomiyaki/recipe.json"
	curl -sf -u admin:admin -H "OCS-APIRequest: true" -X POST "$URL/index.php/apps/cookbook/api/v1/reindex" >/dev/null
	run convert
	run convert --apply
	# The Cookbook API must now serve the converted recipe.
	id=$(curl -sf -u admin:admin -H "OCS-APIRequest: true" "$URL/index.php/apps/cookbook/api/v1/recipes" |
		python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["recipe_id"])')
	curl -sf -u admin:admin -H "OCS-APIRequest: true" "$URL/index.php/apps/cookbook/api/v1/recipes/$id" |
		python3 -c 'import json,sys
r = json.load(sys.stdin)
print("Cookbook now serves:", r["recipeIngredient"], r["recipeInstructions"], r["tool"], sep="\n  ")
assert "120 ml dashi stock (or cold water)" in r["recipeIngredient"], "not converted"'
	echo "Second run must find nothing:"
	run convert | grep -q "nothing to convert"
	run restore "$(ls -d "$XDG_DATA_HOME"/cookbook-metric/backups/*)"
	curl -sf -u admin:admin "$dav/Okonomiyaki/recipe.json" | grep -q "1/2 cup dashi" && echo "Restore OK"
	echo "End-to-end test passed."
	;;
down)
	docker rm -f "$NAME"
	;;
*)
	echo "usage: $0 up|e2e|down" >&2
	exit 1
	;;
esac
