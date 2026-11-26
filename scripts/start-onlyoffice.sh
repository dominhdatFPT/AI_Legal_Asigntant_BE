#!/bin/sh
set -eu

DOCUMENT_ROOT=${ONLYOFFICE_DOCUMENT_ROOT:-/var/www/onlyoffice/documentserver}
NGINX_ROOT=${ONLYOFFICE_NGINX_ROOT:-/etc/onlyoffice/documentserver/nginx}
PLUGIN_DIR="$DOCUMENT_ROOT/sdkjs-plugins/{9DC93CDB-B576-4F0C-B55E-FCC9C48DD007}/translations"

# The AI plugin requests helper translations even when this directory is absent.
# Preserve packaged translations and provide the same English fallback for both
# Vietnamese locale spellings used by Document Server and the plugin.
if [ -d "$PLUGIN_DIR" ]; then
  mkdir -p "$PLUGIN_DIR/helpers"
  for dir in "$PLUGIN_DIR" "$PLUGIN_DIR/helpers"; do
    for locale in vi-VI vi-VN; do
      target="$dir/$locale.json"
      if [ ! -s "$target" ]; then
        if [ -s "$dir/en.json" ]; then
          cp "$dir/en.json" "$target"
        elif [ -s "$dir/en-US.json" ]; then
          cp "$dir/en-US.json" "$target"
        else
          printf '{}\n' > "$target"
        fi
        chmod 644 "$target"
      fi
      gzip -c -9 "$target" > "$target.gz"
    done
  done
fi

# Patch the template before upstream startup generates api.js from it.
# Also patch an existing api.js for restarts and template-free layouts.
patch_api() {
  file=$1
  if grep -q 'legal-office-unload' "$file"; then
    return 0
  fi
  if ! grep -q 'return iframe;' "$file"; then
    printf 'ONLYOFFICE bootstrap: iframe anchor missing in %s\n' "$file" >&2
    return 1
  fi
  sed -i '/return iframe;/i\        iframe.setAttribute("allow", "unload; " + (iframe.getAttribute("allow") || "")); // legal-office-unload' "$file"
}

api="$DOCUMENT_ROOT/web-apps/apps/api/documents/api.js"
if [ ! -f "$api.tpl" ] && [ ! -f "$api" ]; then
  printf 'ONLYOFFICE bootstrap: neither %s.tpl nor %s exists\n' "$api" "$api" >&2
  exit 1
fi
if [ -f "$api.tpl" ]; then
  patch_api "$api.tpl"
fi
if [ -f "$api" ]; then
  patch_api "$api"
  gzip -c -9 "$api" > "$api.gz"
fi

# Static HTML locations declare Cache-Control, so they do not inherit headers
# from the http/server level. Apply the policy in those locations too.
common="$NGINX_ROOT/includes/http-common.conf"
static="$NGINX_ROOT/includes/ds-docservice.conf"
if ! grep -q 'legal-office-unload' "$common"; then
  printf '\n# legal-office-unload\nadd_header Permissions-Policy "unload=(self)" always;\n' >> "$common"
fi
if ! grep -q 'legal-office-unload' "$static"; then
  grep -q 'add_header Cache-Control' "$static"
  sed -i '/add_header Cache-Control/a\  add_header Permissions-Policy "unload=(self)" always; # legal-office-unload' "$static"
fi

if [ -z "${ONLYOFFICE_JWT_SECRET:-}" ]; then
  ONLYOFFICE_JWT_SECRET=$(sha256sum /run/keys/jwt_private.pem | cut -d' ' -f1)
fi
export JWT_SECRET="$ONLYOFFICE_JWT_SECRET"
exec "${ONLYOFFICE_RUN_SCRIPT:-/app/ds/run-document-server.sh}"
