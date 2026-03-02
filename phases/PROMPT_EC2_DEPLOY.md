# PROMPT — PAWBOT EC2 LANDING PAGE + LIVE DEPLOYMENT

You are a full-stack engineer. Your job is to build the Pawbot public landing page and wire up the EC2 server so that `https://pawbot.thecloso.com` shows it the moment someone visits.

The Pawbot codebase is already on the server. The installer script works. You are not touching any of that. Your only job is:

1. Build `index.html` — the landing page
2. Update `nginx.conf` — serve it + all routes
3. Create `launch.sh` — one command to go live
4. Create `pawbot-dashboard.service` — keeps the dashboard alive on reboot

**Read this entire file before writing a single line of code or running a single command.**

---

## WHAT ALREADY EXISTS ON THE SERVER

```
/home/ubuntu/pawbot/
├── install/
│   ├── setup.sh          ← WORKING installer. DO NOT TOUCH.
│   ├── nginx.conf        ← You will replace this
│   └── deploy.sh
├── pawbot/
│   ├── cli/commands.py
│   └── dashboard/
│       ├── server.py     ← FastAPI on port 4000
│       └── ui.html
└── pyproject.toml
```

## WHAT YOU WILL CREATE

```
/home/ubuntu/pawbot/
├── install/
│   ├── web/
│   │   └── index.html    ← NEW — landing page (single file, all CSS+JS inline)
│   ├── nginx.conf        ← REPLACE — full route config
│   └── pawbot-dashboard.service  ← NEW — systemd unit
└── launch.sh             ← NEW — one-command deploy
```

## TARGET URLs WHEN DONE

| URL | Serves |
|-----|--------|
| `https://pawbot.thecloso.com/` | Landing page |
| `https://pawbot.thecloso.com/install` | Raw `setup.sh` (text/plain, curl-pipeable) |
| `https://pawbot.thecloso.com/health` | `200 ok` |
| `https://pawbot.thecloso.com/dashboard` | Proxy → FastAPI port 4000 |
| `https://pawbot.thecloso.com/docs` | 301 → GitHub README |
| `http://pawbot.thecloso.com/` | 301 → HTTPS |

---

## STEP 1 — READ THE SERVER STATE FIRST

Run all of these before writing anything:

```bash
# What's already in nginx?
cat /home/ubuntu/pawbot/install/nginx.conf

# Is nginx running?
sudo systemctl status nginx --no-pager

# Is SSL already provisioned?
ls /etc/letsencrypt/live/pawbot.thecloso.com/ 2>/dev/null || echo "No SSL cert yet"

# Is the dashboard already running?
curl -s http://127.0.0.1:4000/api/health && echo "Dashboard UP" || echo "Dashboard DOWN"

# What port is pawbot using?
grep -r "port\|4000\|uvicorn" /home/ubuntu/pawbot/pawbot/dashboard/server.py | head -10

# Is pawbot installed?
which pawbot && pawbot --version || echo "pawbot not in PATH"
```

Write down exactly what you find. Do not proceed until you've run all of the above.

---

## STEP 2 — BUILD THE LANDING PAGE

**File:** `/home/ubuntu/pawbot/install/web/index.html`

Rules before you start:
- **Single HTML file.** All CSS and JS inline. Zero external `.css` or `.js` files.
- **No build step.** No npm. No webpack. No React.
- **Google Fonts only** — loaded via `<link>` tag.
- **No images** except what can be done in pure CSS or SVG inline.
- **Under 200KB total** — must load fast.

### Aesthetic

**Name of the vibe: "Precision Wild."**

Pawbot is a paw. A developer tool. Something alive on your machine. The landing page should feel like a premium open-source tool — not corporate SaaS, not a startup Y Combinator template. Think: Vercel meets a nature documentary. Clean, confident, a little untamed.

**Exact color palette — use these as CSS variables:**
```css
:root {
  --bg:          #080808;   /* absolute black */
  --surface:     #101010;   /* card surface */
  --border:      #1c1c1c;   /* all borders */
  --text:        #f0f0f0;   /* primary text */
  --muted:       #555555;   /* secondary text */
  --dim:         #2a2a2a;   /* very dim elements */
  --green:       #00e87a;   /* Pawbot brand — electric mint */
  --green-glow:  #00e87a14; /* green tint backgrounds */
  --warm:        #f5f0e8;   /* warm white — used sparingly */
  --code-bg:     #0d1117;   /* terminal/code blocks */
}
```

**Fonts — load both from Google Fonts:**
```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Mono:wght@300;400;500&display=swap" rel="stylesheet">
```

- `'DM Serif Display'` — all headlines, large display text
- `'DM Mono'` — all code, terminal blocks, labels, captions
- Never use system fonts for anything visible

**Layout rules:**
- Max content width: `860px`, centered
- Section padding: `120px 0`
- No nav hamburger menus — single sticky header bar
- All sections fade + slide up on scroll (IntersectionObserver)

---

### SECTION-BY-SECTION SPEC

#### HEADER (sticky)

```
🐾  PAWBOT                               [Get Started →]
```

- Height: `56px`
- Background: `rgba(8,8,8,0.92)` with `backdrop-filter: blur(12px)`
- Border-bottom: `1px solid var(--border)`
- Logo: paw emoji + wordmark in DM Mono, `letter-spacing: 0.12em`
- CTA button: `border: 1px solid var(--green)`, color `var(--green)`, hover fills green with black text

---

#### HERO (full viewport height)

**Left column (55%):**

```
Your personal
AI assistant.

Lives on your machine.
Talks on every device.
```

Headline font: `DM Serif Display`, `clamp(48px, 7vw, 80px)`, line-height `1.1`.
Sub-headline: DM Mono, `18px`, color `var(--muted)`.

Below the headline, the **install command terminal block** — this is the most important element:

```
┌─────────────────────────────────────────────────────────┐
│  $  curl -fsSL pawbot.thecloso.com/install | bash       │
└─────────────────────────────────────────────────────────┘
                                              [Copy]
```

Terminal block styles:
```css
.install-block {
  background: var(--code-bg);
  border: 1px solid #00e87a33;
  border-radius: 6px;
  padding: 18px 24px;
  font-family: 'DM Mono', monospace;
  font-size: 15px;
  color: var(--text);
  position: relative;
  box-shadow: 0 0 40px #00e87a08, 0 20px 40px #00000060;
}
.install-block .prompt { color: var(--green); margin-right: 12px; }
.install-block .cursor::after {
  content: '█';
  color: var(--green);
  animation: blink 1.2s step-end infinite;
}
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }
```

Copy button:
- Position: `absolute top-right` inside the block
- Default text: `Copy`
- After click: `Copied ✓` for 2 seconds, then reverts
- `data-copy="curl -fsSL pawbot.thecloso.com/install | bash"`

**Right column (45%):**

A large typographic paw mark — the letter arrangement renders visually as a paw, using absolute-positioned `span` elements with `DM Mono`, `var(--green)`, opacity `0.12`:

```html
<div class="paw-mark" aria-hidden="true">
  <!-- Three toe beans top row -->
  <span class="toe t1">●</span>
  <span class="toe t2">●</span>
  <span class="toe t3">●</span>
  <!-- Main pad -->
  <span class="pad">🐾</span>
</div>
```

Or alternatively: the word `PAWBOT` set in DM Serif Display at `22vw`, `opacity: 0.03`, rotated `-8deg`, absolutely positioned to bleed off the right edge — purely decorative background text.

**Hero scroll indicator** (bottom center of viewport):
```css
.scroll-hint {
  position: absolute;
  bottom: 40px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  opacity: 0.3;
  animation: bob 2s ease-in-out infinite;
}
@keyframes bob { 0%,100%{transform:translateX(-50%) translateY(0)} 50%{transform:translateX(-50%) translateY(6px)} }
```

---

#### WHAT IT DOES (3 features)

No header needed. Three columns, no icons — use oversized dim numbers instead.

```
   01                      02                      03
   ──────────────────      ──────────────────      ──────────────────
   Runs on your            Connects your           Remembers
   machine.                channels.               everything.

   Your data never         Telegram, WhatsApp,     Memory that grows
   leaves. No cloud.       Email — one command     with every
   No subscriptions.       to connect.             conversation.
```

Card style:
```css
.feature-card {
  border-left: 2px solid var(--border);
  padding-left: 24px;
  transition: border-color 300ms;
}
.feature-card:hover { border-left-color: var(--green); }
.feature-number {
  font-family: 'DM Mono';
  font-size: 11px;
  letter-spacing: 0.15em;
  color: var(--dim);
  margin-bottom: 20px;
}
.feature-title {
  font-family: 'DM Serif Display';
  font-size: 26px;
  color: var(--text);
  margin-bottom: 12px;
}
.feature-body {
  font-family: 'DM Mono';
  font-size: 13px;
  color: var(--muted);
  line-height: 1.7;
}
```

---

#### THREE WAYS TO INSTALL

Section heading in DM Serif Display, `38px`:
```
Three ways in.
```

Three terminal blocks side by side (stack on mobile). Each has a label above in DM Mono uppercase, and a `[Copy]` button:

```
  RECOMMENDED               GITHUB DIRECT             INSPECT FIRST
  ─────────────────────     ─────────────────────     ─────────────────────
  $ curl -fsSL              $ curl -fsSL              $ git clone
    pawbot.thecloso           raw.githubusercontent     https://github.com/
    .com/install              .com/YOUR_ORG/            YOUR_ORG/pawbot
    | bash                    pawbot/main/install/
                              setup.sh | bash           $ bash pawbot/
                                                          install/setup.sh
```

Also available line below:
```
  Also available:   pip install pawbot-ai   [Copy]
```

---

#### TERMINAL DEMO

A fake terminal window. Typewriter animation: lines appear one at a time, each character typed at ~35ms per character. After all lines appear, cursor blinks at last line.

```
┌── pawbot ─────────────────────────────────── ● ● ● ─┐
│                                                      │
│  $ pawbot agent                                      │
│                                                      │
│  > what's on my calendar today?                      │
│  🐾 You have 3 meetings. Standup at 10am,            │
│     design review at 2pm, team sync at 4pm.          │
│     Want me to set reminders?                        │
│                                                      │
│  > yes, 15 minutes before each                       │
│  🐾 Done. 3 reminders set. Also — there's an        │
│     unread PR from yesterday: "review PR #47".       │
│     Add it to today's list?                          │
│                                                      │
│  > yes                                               │
│  🐾 Added. Good morning. ☀️                          │
│                                                      │
│  > _                                                 │
└──────────────────────────────────────────────────────┘
```

Window styling:
```css
.terminal-window {
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 40px 80px #00000080;
  max-width: 620px;
  margin: 0 auto;
}
.terminal-titlebar {
  background: #161616;
  padding: 12px 16px;
  display: flex;
  align-items: center;
  gap: 8px;
  border-bottom: 1px solid var(--border);
}
.traffic-light { width: 12px; height: 12px; border-radius: 50%; }
.tl-red    { background: #ff5f57; }
.tl-yellow { background: #febc2e; }
.tl-green  { background: #28c840; }
.terminal-title {
  margin-left: 8px;
  font-family: 'DM Mono';
  font-size: 12px;
  color: var(--muted);
}
.terminal-body {
  padding: 24px 24px;
  font-family: 'DM Mono';
  font-size: 13px;
  line-height: 1.8;
  min-height: 320px;
}
.line-prompt { color: var(--muted); }
.line-user   { color: var(--text); }
.line-bot    { color: var(--green); }
```

Typewriter JS:
```javascript
const lines = [
  { cls: 'line-prompt', text: '$ pawbot agent' },
  { cls: '', text: '' },
  { cls: 'line-user',   text: '> what\'s on my calendar today?' },
  { cls: 'line-bot',    text: '🐾 You have 3 meetings. Standup at 10am,' },
  { cls: 'line-bot',    text: '   design review at 2pm, team sync at 4pm.' },
  { cls: 'line-bot',    text: '   Want me to set reminders?' },
  { cls: '', text: '' },
  { cls: 'line-user',   text: '> yes, 15 minutes before each' },
  { cls: 'line-bot',    text: '🐾 Done. 3 reminders set.' },
  { cls: '', text: '' },
  { cls: 'line-user',   text: '> yes' },
  { cls: 'line-bot',    text: '🐾 Added. Good morning. ☀️' },
];

async function typeTerminal(container) {
  for (const line of lines) {
    const el = document.createElement('div');
    el.className = line.cls;
    container.appendChild(el);
    for (const char of line.text) {
      el.textContent += char;
      await sleep(32 + Math.random() * 20);
    }
    await sleep(180);
  }
  // Add blinking cursor
  const cursor = document.createElement('div');
  cursor.className = 'line-user cursor';
  cursor.textContent = '> ';
  container.appendChild(cursor);
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// Start typing when section enters viewport
const terminalObserver = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      typeTerminal(document.querySelector('.terminal-body'));
      terminalObserver.unobserve(e.target);
    }
  });
}, { threshold: 0.3 });
terminalObserver.observe(document.querySelector('.terminal-window'));
```

---

#### REQUIREMENTS TABLE

Clean, no borders except bottom dividers.

```
  REQUIRES
  ──────────────────────────────────────────────────────
  Python           3.11 or higher
  Platform         macOS · Linux · Windows (WSL)
  API key          OpenRouter (free) · Anthropic · OpenAI
  Node.js          18+ (only for WhatsApp channel)
  ──────────────────────────────────────────────────────
  That's it.
```

```css
.requirements-table { width: 100%; font-family: 'DM Mono'; font-size: 13px; }
.requirements-table tr { border-bottom: 1px solid var(--border); }
.requirements-table td { padding: 14px 0; }
.requirements-table td:first-child { color: var(--muted); width: 140px; }
.requirements-table td:last-child  { color: var(--text); }
```

---

#### FINAL CTA

Dark section, vertically centered, generous whitespace.

```
         🐾

    Ready to start?

    curl -fsSL pawbot.thecloso.com/install | bash

    [Copy install command]      [View on GitHub →]

    ─────────────────────────────────────────────────
    MIT License  ·  Built for developers  ·  Runs locally
```

Same terminal block styling as hero. Same copy button behavior.

---

#### FOOTER

```
🐾 PAWBOT     pawbot.thecloso.com                    MIT License
              github.com/YOUR_ORG/pawbot
```

Font: DM Mono, `12px`, `var(--muted)`. Border-top: `1px solid var(--border)`. Padding: `32px 0`.

---

### SCROLL REVEAL IMPLEMENTATION

```javascript
// Fade-in all .reveal elements as they enter viewport
const revealObserver = new IntersectionObserver(
  entries => entries.forEach(e => {
    if (e.isIntersecting) {
      e.target.classList.add('visible');
      revealObserver.unobserve(e.target);
    }
  }),
  { threshold: 0.1 }
);
document.querySelectorAll('.reveal').forEach(el => revealObserver.observe(el));
```

```css
.reveal {
  opacity: 0;
  transform: translateY(28px);
  transition: opacity 0.7s ease, transform 0.7s ease;
}
.reveal.visible { opacity: 1; transform: none; }
/* Stagger children */
.reveal-stagger > * { opacity: 0; transform: translateY(20px); transition: opacity 0.6s ease, transform 0.6s ease; }
.reveal-stagger.visible > *:nth-child(1) { opacity:1; transform:none; transition-delay:0s; }
.reveal-stagger.visible > *:nth-child(2) { opacity:1; transform:none; transition-delay:0.12s; }
.reveal-stagger.visible > *:nth-child(3) { opacity:1; transform:none; transition-delay:0.24s; }
```

### HERO PAGE-LOAD ANIMATIONS

```css
/* These run on page load — no JS needed */
.hero-headline { animation: rise 0.9s cubic-bezier(.16,1,.3,1) 0.1s both; }
.hero-sub      { animation: rise 0.9s cubic-bezier(.16,1,.3,1) 0.25s both; }
.hero-terminal { animation: rise 0.9s cubic-bezier(.16,1,.3,1) 0.4s both; }
.hero-cta-row  { animation: rise 0.9s cubic-bezier(.16,1,.3,1) 0.55s both; }
@keyframes rise {
  from { opacity: 0; transform: translateY(32px); }
  to   { opacity: 1; transform: none; }
}
```

### MOBILE RESPONSIVE

```css
@media (max-width: 680px) {
  .hero { flex-direction: column; }
  .hero-decoration { display: none; }
  .install-methods-grid { grid-template-columns: 1fr; }
  .features-grid { grid-template-columns: 1fr; gap: 40px; }
  .hero-headline { font-size: clamp(36px, 10vw, 56px); }
}
```

---

## STEP 3 — REPLACE `nginx.conf`

**File:** `/home/ubuntu/pawbot/install/nginx.conf`

Read the current file first: `cat /home/ubuntu/pawbot/install/nginx.conf`

Then replace it entirely with this — preserving any routes that already work and are not listed here:

```nginx
# Pawbot — nginx config for pawbot.thecloso.com
# Generated by launch.sh — do not edit manually

# HTTP → HTTPS redirect
server {
    listen 80;
    listen [::]:80;
    server_name pawbot.thecloso.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name pawbot.thecloso.com;

    # SSL — managed by certbot
    ssl_certificate     /etc/letsencrypt/live/pawbot.thecloso.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/pawbot.thecloso.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_session_cache   shared:SSL:10m;

    # Security headers
    add_header X-Frame-Options      SAMEORIGIN always;
    add_header X-Content-Type-Options nosniff always;

    # Web root = landing page
    root  /var/www/pawbot/web;
    index index.html;

    # ── / — Landing page ──────────────────────────────────────
    location = / {
        try_files /index.html =404;
        add_header Cache-Control "public, max-age=1800";
    }

    # ── /install — Installer script (MUST be text/plain) ──────
    location = /install {
        alias /var/www/pawbot/setup.sh;
        default_type text/plain;
        add_header Cache-Control "no-cache, no-store, must-revalidate";
        add_header Content-Disposition "inline; filename=setup.sh";
    }

    # Also catch /install.sh
    location = /install.sh {
        alias /var/www/pawbot/setup.sh;
        default_type text/plain;
        add_header Cache-Control "no-cache";
    }

    # ── /health — Uptime check ────────────────────────────────
    location = /health {
        return 200 "ok\n";
        add_header Content-Type text/plain;
        access_log off;
    }

    # ── /dashboard — Proxy to FastAPI ─────────────────────────
    location /dashboard {
        proxy_pass         http://127.0.0.1:4000;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade    $http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host       $host;
        proxy_set_header   X-Real-IP  $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }

    # API routes (dashboard AJAX calls)
    location /api/ {
        proxy_pass         http://127.0.0.1:4000/api/;
        proxy_http_version 1.1;
        proxy_set_header   Host       $host;
        proxy_set_header   X-Real-IP  $remote_addr;
        proxy_read_timeout 60s;
    }

    # ── /docs — GitHub redirect ───────────────────────────────
    location = /docs {
        return 301 https://github.com/YOUR_ORG/pawbot#readme;
    }

    # ── /releases — GitHub releases redirect ─────────────────
    location = /releases {
        return 301 https://github.com/YOUR_ORG/pawbot/releases;
    }

    # ── /github — Repo redirect ───────────────────────────────
    location = /github {
        return 301 https://github.com/YOUR_ORG/pawbot;
    }

    # Static asset caching
    location ~* \.(css|js|woff2?|ttf|ico|svg|webp|png|jpg)$ {
        expires 30d;
        add_header Cache-Control "public, max-age=2592000, immutable";
        access_log off;
    }

    # Logs
    access_log /var/log/nginx/pawbot_access.log;
    error_log  /var/log/nginx/pawbot_error.log warn;
}
```

---

## STEP 4 — CREATE `launch.sh`

**File:** `/home/ubuntu/pawbot/launch.sh`

One command to go from "code is on the server" to "site is live."

```bash
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
```

---

## STEP 5 — CREATE SYSTEMD SERVICE

**File:** `/home/ubuntu/pawbot/install/pawbot-dashboard.service`

```ini
[Unit]
Description=Pawbot Dashboard
Documentation=https://pawbot.thecloso.com/docs
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/pawbot
ExecStart=/home/ubuntu/.local/bin/pawbot dashboard --host 127.0.0.1 --port 4000 --no-browser
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=pawbot-dashboard
Environment=HOME=/home/ubuntu
Environment=PATH=/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
```

---

## STEP 6 — VERIFY EVERYTHING WORKS

After running `bash ~/pawbot/launch.sh`, verify each endpoint manually:

```bash
# 1. Landing page — must return HTML
curl -s https://pawbot.thecloso.com/ | head -5
# Expected: <!DOCTYPE html>...

# 2. Install script — must be raw bash (text/plain)
curl -sI https://pawbot.thecloso.com/install | grep -i content-type
# Expected: content-type: text/plain

curl -fsSL https://pawbot.thecloso.com/install | head -3
# Expected: #!/usr/bin/env bash

# 3. Health endpoint
curl -s https://pawbot.thecloso.com/health
# Expected: ok

# 4. HTTP forces HTTPS
curl -sI http://pawbot.thecloso.com/ | grep -i location
# Expected: Location: https://pawbot.thecloso.com/

# 5. Dashboard proxied correctly
curl -s -o /dev/null -w "%{http_code}" https://pawbot.thecloso.com/dashboard
# Expected: 200

# 6. Docs redirects
curl -sI https://pawbot.thecloso.com/docs | grep -i location
# Expected: Location: https://github.com/...

# 7. The actual install works end to end
curl -fsSL https://pawbot.thecloso.com/install | bash
# Expected: installer runs, guides through setup

# 8. Status summary
bash ~/pawbot/launch.sh --status
```

---

## FILE SUMMARY

Create exactly these 4 files. Nothing else.

| File | Action |
|------|--------|
| `/home/ubuntu/pawbot/install/web/index.html` | **CREATE** — full landing page |
| `/home/ubuntu/pawbot/install/nginx.conf` | **REPLACE** — full route config |
| `/home/ubuntu/pawbot/install/pawbot-dashboard.service` | **CREATE** — systemd unit |
| `/home/ubuntu/pawbot/launch.sh` | **CREATE** — one-command deploy |

**Do not touch:**
- `install/setup.sh` — working installer, leave it alone
- Any file in `pawbot/` — working codebase, leave it alone

---

## RULES

1. `index.html` is **one file** — all CSS and JS inline, no external files except Google Fonts `<link>`
2. `/install` must serve `Content-Type: text/plain` — anything else breaks `curl ... | bash`
3. The dashboard runs on `127.0.0.1:4000` only — nginx proxies it, never expose it directly
4. `launch.sh` is **idempotent** — running it twice doesn't break anything
5. `launch.sh --status` must work without deploying anything
6. The landing page must load in under 2 seconds on a typical connection

---

## DEFINITION OF DONE

- [ ] `bash ~/pawbot/launch.sh` runs without errors
- [ ] `https://pawbot.thecloso.com` shows the landing page
- [ ] `https://pawbot.thecloso.com/install` returns `text/plain` with the bash script
- [ ] `curl -fsSL https://pawbot.thecloso.com/install | bash` launches the installer
- [ ] `https://pawbot.thecloso.com/health` returns `200 ok`
- [ ] `https://pawbot.thecloso.com/dashboard` shows the dashboard
- [ ] `http://pawbot.thecloso.com` redirects to HTTPS
- [ ] Dashboard survives a server reboot (systemd service active)
- [ ] Copy buttons on the landing page work (clipboard API)
- [ ] Typewriter animation plays in the terminal demo section
- [ ] `bash ~/pawbot/launch.sh --status` shows all green
