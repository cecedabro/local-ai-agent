#!/usr/bin/env bash
# Download offline reference docs for the agent into ~/docs (on the PVC, not in git).
# Search them with `rg -i <term> ~/docs`; AGENTS.md of a project should say so.
# Re-running refreshes them.
set -euo pipefail

DEST="${1:-${HOME}/docs}"
mkdir -p "${DEST}"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

# sparse_clone <name> <repo-url> <path-inside-repo>
sparse_clone() {
  local name="$1" url="$2" path="$3"
  git clone -q --depth 1 --filter=blob:none --sparse "${url}" "${tmp}/${name}"
  git -C "${tmp}/${name}" sparse-checkout set "${path}"
  rm -rf "${DEST:?}/${name}"
  cp -R "${tmp}/${name}/${path}" "${DEST}/${name}"
  echo "ok ${name}"
}

sparse_clone fastapi    https://github.com/fastapi/fastapi.git            docs/en/docs
sparse_clone jinja      https://github.com/pallets/jinja.git              docs
sparse_clone sqlalchemy https://github.com/sqlalchemy/sqlalchemy.git     doc/build
sparse_clone alembic    https://github.com/sqlalchemy/alembic.git         docs/build
sparse_clone htmx       https://github.com/bigskysoftware/htmx.git        www/content
sparse_clone uplot      https://github.com/leeoniya/uPlot.git             docs

# Python standard library docs as plain text.
# The zip name carries the patch version, so read it off the download page.
zip_name="$(curl -fsSL https://docs.python.org/3.11/download.html | grep -o 'python-3\.11\.[0-9]*-docs-text\.zip' | head -1)"
curl -fsSL "https://docs.python.org/3.11/archives/${zip_name}" -o "${tmp}/py.zip"
rm -rf "${DEST:?}/python"
unzip -q "${tmp}/py.zip" -d "${tmp}/py"
mv "${tmp}"/py/python-3.11*-docs-text "${DEST}/python"
echo "ok python"
