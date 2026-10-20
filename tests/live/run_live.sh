#!/usr/bin/env bash
# Live tests: the real InvenTree (with this plugin) and a real WordPress (with the inventree-shop
# plugin and stand-ins for WooCommerce) talking to each other. CI runs this; so can you.
#
#   tests/live/run_live.sh <InvenTree checkout> <python> <work dir> [--no-wordpress]
#
# 1. A fresh InvenTree database: migrate, activate the plugin, migrate again.
# 2. tests/integration/check_inventree.py - every InvenTree-side flow (sync, orders, credit,
#    builds, quotes, PDFs, charges, pricing providers, shipments).
# 3. Unless --no-wordpress: InvenTree's dev server on 127.0.0.1:8124, WordPress (from GitHub,
#    SQLite instead of MySQL) in <work dir>/wp, then wordpress/tests/test-gateway.php and
#    test-quotes.php against it.
#
# Needs: git, php (with pdo_sqlite, curl, mbstring), and the InvenTree requirements plus
# `pip install -e <this repo> pypdf` in <python>'s environment.

set -euo pipefail

INVENTREE="$(cd "$1" && pwd)"
PY="$2"
WORK="$(mkdir -p "$3" && cd "$3" && pwd)"
WORDPRESS=1
[ "${4:-}" = "--no-wordpress" ] && WORDPRESS=0
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
WP_VERSION="${WP_VERSION:-6.9.10}"
SQLITE_VERSION="${SQLITE_VERSION:-v3.1.0}"
PORT=8124

say() { printf '\n=== %s ===\n' "$*"; }

export INVENTREE_DB_ENGINE=sqlite3
export INVENTREE_DB_NAME="$WORK/inventree.sqlite3"
export INVENTREE_MEDIA_ROOT="$WORK/media"
export INVENTREE_STATIC_ROOT="$WORK/static"
export INVENTREE_BACKUP_DIR="$WORK/backup"
export INVENTREE_CONFIG_FILE="$WORK/config.yaml"
export INVENTREE_SECRET_KEY="${INVENTREE_SECRET_KEY:-live-test-secret-key-live-test-secret-key}"
export INVENTREE_PLUGINS_ENABLED=True INVENTREE_PLUGIN_TESTING=True INVENTREE_PLUGIN_TESTING_SETUP=True
export INVENTREE_SITE_URL=https://inventree.example.com INVENTREE_ALLOWED_HOSTS='*' INVENTREE_LOG_LEVEL=WARNING

cd "$INVENTREE/src/backend/InvenTree"
rm -rf "$INVENTREE_DB_NAME" "$INVENTREE_MEDIA_ROOT"
mkdir -p "$INVENTREE_MEDIA_ROOT" "$INVENTREE_STATIC_ROOT" "$INVENTREE_BACKUP_DIR"

say "InvenTree database"
"$PY" manage.py migrate --noinput > "$WORK/migrate.log" 2>&1 || { tail -40 "$WORK/migrate.log"; exit 1; }
"$PY" manage.py shell -c "from plugin.models import PluginConfig; PluginConfig.objects.update_or_create(key='woocommerce', defaults={'active': True})" > /dev/null
"$PY" manage.py migrate --noinput 2>&1 | grep -E "inventree_woocommerce\.0" || true

say "InvenTree integration checks"
"$PY" manage.py shell -c "exec(open('$REPO/tests/integration/check_inventree.py').read())" 2>&1 \
    | grep -vE "INVE-W|WARNING|warning|objects imported" | tee "$WORK/integration.log" | tail -25
grep -q "ALL INTEGRATION CHECKS PASSED" "$WORK/integration.log" || { echo "Integration checks FAILED"; exit 1; }

[ "$WORDPRESS" -eq 1 ] || exit 0

say "WordPress $WP_VERSION (SQLite)"
WP="$WORK/wp"
if [ ! -f "$WP/core/wp-load.php" ]; then
    rm -rf "$WP" && mkdir -p "$WP"
    git -c advice.detachedHead=false clone -q --depth 1 --branch "$WP_VERSION" https://github.com/WordPress/WordPress "$WP/core"
    git -c advice.detachedHead=false clone -q --depth 1 --branch "$SQLITE_VERSION" https://github.com/WordPress/sqlite-database-integration "$WP/sqlite"
fi
SQLITE_PLUGIN="$WP/sqlite/packages/plugin-sqlite-database-integration"
[ -d "$SQLITE_PLUGIN" ] || SQLITE_PLUGIN="$WP/sqlite"   # older releases kept the plugin at the root
sed -e "s#{SQLITE_IMPLEMENTATION_FOLDER_PATH}#$SQLITE_PLUGIN#" -e "s#{SQLITE_PLUGIN}#sqlite-database-integration/load.php#g" \
    "$SQLITE_PLUGIN/db.copy" > "$WP/core/wp-content/db.php"
rm -rf "$WP/db" && mkdir -p "$WP/db"
cat > "$WP/core/wp-config.php" <<EOF
<?php
define('DB_NAME', 'wp'); define('DB_USER', 'x'); define('DB_PASSWORD', 'x'); define('DB_HOST', 'localhost');
define('DB_CHARSET', 'utf8'); define('DB_COLLATE', '');
define('DB_DIR', '$WP/db'); define('DB_FILE', 'wp.sqlite');
\$table_prefix = 'wp_';
define('WP_DEBUG', true); define('WP_DEBUG_DISPLAY', true);
foreach (array('AUTH_KEY', 'SECURE_AUTH_KEY', 'LOGGED_IN_KEY', 'NONCE_KEY', 'AUTH_SALT', 'SECURE_AUTH_SALT', 'LOGGED_IN_SALT', 'NONCE_SALT') as \$k) { define(\$k, 'live-test-' . \$k); }
define('WP_HOME', 'http://localhost'); define('WP_SITEURL', 'http://localhost');
if (!defined('ABSPATH')) define('ABSPATH', __DIR__ . '/');
require_once ABSPATH . 'wp-settings.php';
EOF
php -r "define('WP_INSTALLING', true); \$_SERVER['HTTP_HOST'] = 'localhost'; \$_SERVER['REQUEST_URI'] = '/';
  require '$WP/core/wp-load.php'; require_once ABSPATH . 'wp-admin/includes/upgrade.php';
  if (!is_blog_installed()) { wp_install('Test', 'admin', 'admin@example.com', true, '', 'pass'); } echo \"WordPress installed\n\";"

say "InvenTree server on 127.0.0.1:$PORT"
"$PY" manage.py runserver "127.0.0.1:$PORT" --noreload > "$WORK/server.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
for _ in $(seq 1 90); do
    curl -s -o /dev/null "http://127.0.0.1:$PORT/api/" && break
    sleep 1
done
curl -s -o /dev/null "http://127.0.0.1:$PORT/api/" || { tail -40 "$WORK/server.log"; echo "InvenTree didn't start"; exit 1; }

say "WordPress plugin tests"
cd "$REPO/wordpress/tests"
php test-gateway.php "$WP/core" | tee "$WORK/wp-gateway.log" | tail -3
grep -q "ALL GATEWAY CHECKS PASSED" "$WORK/wp-gateway.log" || { echo "Gateway checks FAILED"; grep FAIL "$WORK/wp-gateway.log"; exit 1; }
# customer 77 and quote QR-261019-TEST were made by the integration checks (secret "linksecret")
php test-quotes.php "$WP/core" "http://127.0.0.1:$PORT" linksecret 77 QR-261019-TEST | tee "$WORK/wp-quotes.log" | tail -3
grep -q "ALL QUOTE CHECKS PASSED" "$WORK/wp-quotes.log" || { echo "Quote checks FAILED"; grep FAIL "$WORK/wp-quotes.log"; exit 1; }

say "All live tests passed"
