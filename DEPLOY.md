# 🐾 Pawbot — Deploy Landing Page + Installer on Ubuntu EC2

Complete copy-paste guide. All config files included inline.

---

## 1. SSH Into Your Server

```bash
ssh ubuntu@<YOUR_EC2_IP>
```

---

## 2. Install Dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y nginx certbot python3-certbot-nginx git curl
```

---

## 3. Clone the Repo

```bash
cd ~
git clone https://github.com/AiWaldoh/pawbot.git
cd ~/pawbot
```

> Already cloned? Run `cd ~/pawbot && git pull origin main`

---

## 4. Create Web Root + Copy Files

```bash
# Create directories
sudo mkdir -p /var/www/pawbot/web
sudo chown -R $USER:$USER /var/www/pawbot

# Copy landing page
cp ~/pawbot/install/web/index.html /var/www/pawbot/web/index.html

# Copy installer script
cp ~/pawbot/install/setup.sh /var/www/pawbot/setup.sh
chmod 644 /var/www/pawbot/setup.sh
```

---

## 5. Install Nginx Config

```bash
sudo nano /etc/nginx/sites-available/pawbot
```

Paste this entire config:

```nginx
# Pawbot — nginx config for pawbot.thecloso.com

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

    # Web root
    root  /var/www/pawbot/web;
    index index.html;

    # Landing page
    location = / {
        try_files /index.html =404;
        add_header Cache-Control "public, max-age=1800";
    }

    # Installer script — text/plain for curl | bash
    location = /install {
        alias /var/www/pawbot/setup.sh;
        default_type text/plain;
        add_header Cache-Control "no-cache, no-store, must-revalidate";
        add_header Content-Disposition "inline; filename=setup.sh";
    }

    location = /install.sh {
        alias /var/www/pawbot/setup.sh;
        default_type text/plain;
        add_header Cache-Control "no-cache";
    }

    # Health check
    location = /health {
        return 200 "ok\n";
        add_header Content-Type text/plain;
        access_log off;
    }

    # GitHub redirects
    location = /docs {
        return 301 https://github.com/AiWaldoh/pawbot#readme;
    }

    location = /releases {
        return 301 https://github.com/AiWaldoh/pawbot/releases;
    }

    location = /github {
        return 301 https://github.com/AiWaldoh/pawbot;
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

Save and exit (`Ctrl+X`, `Y`, `Enter`).

Now enable the site:

```bash
sudo ln -sf /etc/nginx/sites-available/pawbot /etc/nginx/sites-enabled/pawbot
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
```

You should see: `syntax is ok` / `test is successful`.

---

## 6. Get SSL Certificate

> **Important:** DNS for `pawbot.thecloso.com` must already point to this server's IP.

```bash
# Start nginx on port 80 first (certbot needs it)
sudo systemctl start nginx

# Request certificate
sudo certbot --nginx \
    -d pawbot.thecloso.com \
    --non-interactive \
    --agree-tos \
    -m admin@thecloso.com \
    --redirect
```

Verify the cert was issued:

```bash
sudo ls /etc/letsencrypt/live/pawbot.thecloso.com/
# Should show: cert.pem  chain.pem  fullchain.pem  privkey.pem
```

---

## 7. Start Nginx

```bash
sudo systemctl reload nginx
sudo systemctl enable nginx
```

---

## 8. Verify Everything

```bash
# Landing page — should return HTML
curl -s https://pawbot.thecloso.com/ | head -3

# Installer — Content-Type must be text/plain
curl -sI https://pawbot.thecloso.com/install | grep -i content-type

# Installer content — should start with #!/usr/bin/env bash
curl -fsSL https://pawbot.thecloso.com/install | head -3

# Health check — should return "ok"
curl https://pawbot.thecloso.com/health

# HTTP → HTTPS redirect
curl -sI http://pawbot.thecloso.com/ | grep -i location

# GitHub docs redirect
curl -sI https://pawbot.thecloso.com/docs | grep -i location
```

Expected results:

| URL | Expected |
|-----|----------|
| `https://pawbot.thecloso.com/` | `200` — HTML landing page |
| `https://pawbot.thecloso.com/install` | `200` — `text/plain`, bash script |
| `https://pawbot.thecloso.com/health` | `200` — `ok` |
| `http://pawbot.thecloso.com/` | `301` → `https://` |
| `https://pawbot.thecloso.com/docs` | `301` → GitHub |

---

## Update Later

When you push changes to the landing page or installer:

```bash
cd ~/pawbot && git pull origin main
cp install/web/index.html /var/www/pawbot/web/index.html
cp install/setup.sh /var/www/pawbot/setup.sh
```

No nginx reload needed — just copy the files.

---

## Troubleshooting

```bash
# Nginx config syntax error
sudo nginx -t

# Nginx logs
sudo tail -50 /var/log/nginx/pawbot_error.log

# SSL cert won't issue — check DNS first
dig +short pawbot.thecloso.com

# Renew SSL cert
sudo certbot renew

# Restart nginx completely
sudo systemctl restart nginx
```
