#!/usr/bin/env bash
# =============================================================================
# 🐾 Pawbot — EC2 Launch Script
# Usage:
#   bash ~/pawbot/launch.sh           # Full deploy + start everything
#   bash ~/pawbot/launch.sh --restart # Restart services only
#   bash ~/pawbot/launch.sh --status  # Check what's running
# =============================================================================
set -euo pipefail

# Colours
G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; C='\033[0;36m'; DIM='\033[2m'; B='\033[1m'; N='\033[0m'
ok()   { echo -e "  ${G}✓${N}  $1"; }
warn() { echo -e "  ${Y}⚠${N}  $1"; }
fail() { echo -e "\n  ${R}✗  ERROR: $1${N}\n"; exit 1; }
info() { echo -e "  ${C}→${N}  $1"; }
hr()   { echo -e "  ${DIM}────────────────────────────────────────────${N}"; }

PAWBOT_DIR="$(cd "$(dirname "$0")" && pwd)"
WEB_ROOT="/var/www/pawbot"
DASH_PORT=4000
DASH_PID_FILE="/tmp/pawbot-dashboard.pid"
DOMAIN="pawbot.thecloso.com"

echo ""
echo -e "${B}  🐾  Pawbot Launch${N}"
hr; echo ""

# ─── --status ────────────────────────────────────────────────────────────────
if [ "${1:-}" = "--status" ]; then
    echo -e "  ${B}Service Status${N}"; echo ""
    systemctl is-active --quiet nginx 2>/dev/null && ok "nginx running" || warn "nginx not running"
    if [ -f "$DASH_PID_FILE" ] && kill -0 "$(cat "$DASH_PID_FILE")" 2>/dev/null; then
        ok "Dashboard running  (PID $(cat "$DASH_PID_FILE"))"
    else
        warn "Dashboard not running"
    fi
    echo ""
    for endpoint in "/" "/install" "/health"; do
        CODE=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 5 "https://$DOMAIN$endpoint")
        [ "$CODE" = "200" ] || [ "$CODE" = "301" ] \
            && ok "HTTP $CODE  https://$DOMAIN$endpoint" \
            || warn "HTTP $CODE  https://$DOMAIN$endpoint"
    done
    echo ""; exit 0
fi

# ─── --restart: skip file deployment ─────────────────────────────────────────
DEPLOY=true
[ "${1:-}" = "--restart" ] && DEPLOY=false

if $DEPLOY; then
    # 1. Ensure web root exists
    info "Creating web directories..."
    sudo mkdir -p "$WEB_ROOT/web"
    sudo chown -R "$USER:$USER" "$WEB_ROOT"
    ok "Directories ready:  $WEB_ROOT"

    # 2. Deploy landing page
    info "Deploying landing page..."
    [ -f "$PAWBOT_DIR/install/web/index.html" ] \
        || fail "Landing page not found: $PAWBOT_DIR/install/web/index.html"
    cp "$PAWBOT_DIR/install/web/index.html" "$WEB_ROOT/web/index.html"
    ok "index.html  →  $WEB_ROOT/web/index.html"

    # 3. Deploy installer script
    info "Deploying install script..."
    [ -f "$PAWBOT_DIR/install/setup.sh" ] \
        || fail "setup.sh not found: $PAWBOT_DIR/install/setup.sh"
    cp "$PAWBOT_DIR/install/setup.sh" "$WEB_ROOT/setup.sh"
    chmod 644 "$WEB_ROOT/setup.sh"
    ok "setup.sh  →  $WEB_ROOT/setup.sh"

    # 4. Install nginx config
    info "Installing nginx configuration..."
    [ -f "$PAWBOT_DIR/install/nginx.conf" ] \
        || fail "nginx.conf not found: $PAWBOT_DIR/install/nginx.conf"
    sudo cp "$PAWBOT_DIR/install/nginx.conf" /etc/nginx/sites-available/pawbot
    sudo ln -sf /etc/nginx/sites-available/pawbot /etc/nginx/sites-enabled/pawbot
    sudo rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
    sudo nginx -t 2>&1 | grep -v "^$" | head -5
    ok "nginx config installed and valid"

    # 5. SSL certificate
    CERT="/etc/letsencrypt/live/$DOMAIN/fullchain.pem"
    if [ ! -f "$CERT" ]; then
        info "Requesting SSL certificate (Let's Encrypt)..."
        sudo systemctl start nginx 2>/dev/null || true
        sleep 2
        sudo certbot --nginx \
            -d "$DOMAIN" \
            --non-interactive \
            --agree-tos \
            -m admin@thecloso.com \
            --redirect 2>&1 | tail -8
        [ -f "$CERT" ] && ok "SSL certificate issued" || fail "SSL cert failed — check certbot output"
    else
        ok "SSL certificate exists  ($(sudo openssl x509 -enddate -noout -in "$CERT" | cut -d= -f2))"
    fi

    # 6. Reload nginx
    info "Reloading nginx..."
    sudo systemctl reload nginx
    ok "nginx reloaded"

    # 7. Install systemd service (if available)
    SERVICE_FILE="$PAWBOT_DIR/install/pawbot-dashboard.service"
    if [ -f "$SERVICE_FILE" ] && command -v systemctl &>/dev/null; then
        info "Installing systemd service..."
        sudo cp "$SERVICE_FILE" /etc/systemd/system/pawbot-dashboard.service
        sudo systemctl daemon-reload
        sudo systemctl enable pawbot-dashboard 2>/dev/null
        ok "pawbot-dashboard service enabled (auto-starts on reboot)"
    fi
fi

# 8. Start/restart dashboard
info "Starting Pawbot dashboard..."

# Stop old instance
if [ -f "$DASH_PID_FILE" ]; then
    OLD_PID=$(cat "$DASH_PID_FILE")
    kill "$OLD_PID" 2>/dev/null && sleep 1 && ok "Stopped old dashboard (PID $OLD_PID)" || true
    rm -f "$DASH_PID_FILE"
fi

# Ensure pawbot is in PATH
export PATH="$HOME/.local/bin:$PATH"
command -v pawbot &>/dev/null || fail "pawbot command not found. Run: pip install -e $PAWBOT_DIR"

# Start dashboard
nohup pawbot dashboard \
    --host 127.0.0.1 \
    --port $DASH_PORT \
    --no-browser \
    > /tmp/pawbot-dashboard.log 2>&1 &
echo $! > "$DASH_PID_FILE"

# Wait for ready
info "Waiting for dashboard to be ready..."
for i in $(seq 1 15); do
    curl -s "http://127.0.0.1:$DASH_PORT/api/health" > /dev/null 2>&1 && break
    sleep 1
done
curl -s "http://127.0.0.1:$DASH_PORT/api/health" > /dev/null 2>&1 \
    && ok "Dashboard ready on 127.0.0.1:$DASH_PORT" \
    || warn "Dashboard may not be ready — check: tail /tmp/pawbot-dashboard.log"

# 9. Final endpoint checks
echo ""
info "Verifying live endpoints..."
sleep 2

check() {
    local url="$1" want="$2"
    CODE=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 8 "$url")
    [ "$CODE" = "$want" ] && ok "HTTP $CODE  $url" || warn "HTTP $CODE  $url  (expected $want)"
}

check "https://$DOMAIN/"         "200"
check "https://$DOMAIN/install"  "200"
check "https://$DOMAIN/health"   "200"
check "https://$DOMAIN/dashboard" "200"
check "https://$DOMAIN/docs"     "301"

# Verify install script is text/plain
CTYPE=$(curl -skI "https://$DOMAIN/install" | grep -i "^content-type" | tr -d '\r')
echo "$CTYPE" | grep -qi "text/plain" \
    && ok "/install Content-Type: text/plain ✓" \
    || warn "/install Content-Type wrong: $CTYPE  — curl pipe may not work"

# Done
echo ""
hr
echo -e "${B}  🐾  Pawbot is live${N}"
hr; echo ""
echo "    🌐  https://$DOMAIN"
echo "    🔧  https://$DOMAIN/dashboard"
echo "    📦  curl -fsSL https://$DOMAIN/install | bash"
echo "    ❤️   https://$DOMAIN/health"
echo ""
echo -e "    ${DIM}Logs:   tail -f /tmp/pawbot-dashboard.log${N}"
echo -e "    ${DIM}Nginx:  tail -f /var/log/nginx/pawbot_access.log${N}"
echo -e "    ${DIM}Stop:   kill \$(cat $DASH_PID_FILE)${N}"
echo ""
