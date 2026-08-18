# Nginx Configuration Guide

## Where Your Nginx Files Are

On your server (`joshi@aiagent`), Nginx config files are in:

```bash
/etc/nginx/sites-available/
```

### Check What Nginx Files Exist

```bash
ls -la /etc/nginx/sites-available/
```

You should see files like:
- `luna-backend.conf`
- `pm-frontend.conf`
- `ats-frontend.conf`
- etc.

### View Your Luna-Backend Nginx Config

```bash
cat /etc/nginx/sites-available/luna-backend.conf
```

This shows the exact Nginx configuration that was created during deployment.

---

## What the Nginx Config Does

The template file at `app/templates/nginx/reverse_proxy.conf.j2` contains:

### Main Features

1. **HTTP → HTTPS Redirect**
   - All HTTP traffic redirects to HTTPS
   - Let's Encrypt certificate (if domain used)

2. **Security Headers**
   - `X-Frame-Options: SAMEORIGIN` — prevents clickjacking
   - `X-Content-Type-Options: nosniff` — prevents MIME sniffing
   - `Strict-Transport-Security` — forces HTTPS (1 year)

3. **Rate Limiting**
   - 20 requests/second per IP
   - Burst of 50 allowed

4. **Proxying**
   - Main location `/` → proxies to `localhost:{port}`
   - Special `/ws` location → WebSocket proxying
   - Static files cached for 30 days

5. **Logging**
   - Access logs: `/var/log/nginx/{app_name}.access.log`
   - Error logs: `/var/log/nginx/{app_name}.error.log`

6. **Gzip Compression**
   - Compresses JSON, JavaScript, CSS, SVG, etc.

---

## Check if Nginx is Running

```bash
sudo systemctl status nginx
```

You should see:
```
● nginx.service - A high performance web server and reverse proxy server
   Loaded: loaded (/lib/systemd/nginx.service)
   Active: active (running) since ...
```

---

## View Nginx Logs

### All Nginx access logs
```bash
tail -f /var/log/nginx/access.log
```

### Specific app logs
```bash
tail -f /var/log/nginx/luna-backend.access.log
tail -f /var/log/nginx/luna-backend.error.log
```

### Check for Nginx errors
```bash
tail -f /var/log/nginx/error.log
```

---

## Test Nginx Configuration

After making changes:

```bash
sudo nginx -t
```

Should show:
```
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration will be successful
```

---

## Reload Nginx (Apply Changes)

```bash
sudo systemctl reload nginx
```

Or manually:
```bash
sudo nginx -s reload
```

---

## Nginx Directory Structure

```
/etc/nginx/
├── nginx.conf                 # Main config
├── sites-available/           # Available site configs
│   ├── luna-backend.conf     # Luna backend (created during deploy)
│   ├── pm-frontend.conf      # PM frontend (created during deploy)
│   └── ats-frontend.conf     # ATS frontend (created during deploy)
├── sites-enabled/            # Symlinks to active sites (auto-created by system)
│   ├── luna-backend.conf
│   ├── pm-frontend.conf
│   └── ats-frontend.conf
├── ssl/
│   └── dhparam.pem           # DH parameters for SSL
└── conf.d/
    └── (additional configs)
```

---

## For Your Multi-App Setup

Each app has its own Nginx config:

```bash
/etc/nginx/sites-available/luna-backend.conf     # Proxies to localhost:3000
/etc/nginx/sites-available/pm-backend.conf       # Proxies to localhost:3001
/etc/nginx/sites-available/ats-backend.conf      # Proxies to localhost:3002
```

Each one is independent and can be:
- ✅ Enabled (symlink in sites-enabled/)
- ✅ Disabled (remove symlink)
- ✅ Modified
- ✅ Reloaded without restarting others

---

## Manage Nginx Sites

### Enable a site (if not already enabled)
```bash
sudo ln -s /etc/nginx/sites-available/luna-backend.conf /etc/nginx/sites-enabled/
sudo systemctl reload nginx
```

### Disable a site (remove from sites-enabled/)
```bash
sudo rm /etc/nginx/sites-enabled/luna-backend.conf
sudo systemctl reload nginx
```

### View which sites are enabled
```bash
ls /etc/nginx/sites-enabled/
```

---

## During Your Deployment

When you deploy an app, the system:

1. ✅ Asks if you want Nginx configured
   - You can say: **yes** (auto-configure), **no** (skip), or provide domain/IP

2. ✅ If you say **yes**:
   - Installs Nginx (if not already)
   - Generates config from template
   - Tests config for syntax errors
   - Enables site (creates symlink)
   - Reloads Nginx

3. ✅ If you say **no**:
   - App runs directly on port (e.g., `localhost:3000`)
   - Nginx not configured
   - Can configure later manually

---

## Configure Nginx Later (If You Skipped)

If you skipped Nginx during deployment and want to add it later:

**Tell AI**:
```
"setup nginx for luna-backend"
```

**Or manually**:
```bash
# View the deployment context to see app details
cat deployment_context.json | grep -E "app_name|port|domain"

# Then you can manually create the config or ask AI to do it
```

---

## Nginx Config Template Variables

When AI generates your Nginx config, it uses these variables:

- `{{ domain }}` — Your domain or IP address
- `{{ port }}` — Your app's internal port
- `{{ app_name }}` — App name (used for logs)

For example, for `luna-backend` on port 3000:

```nginx
# Original template
server_name {{ domain }};  # Becomes: server_name pm.meetri.in;
proxy_pass http://127.0.0.1:{{ port }};  # Becomes: proxy_pass http://127.0.0.1:3000;
access_log /var/log/nginx/{{ app_name }}.access.log;  # Becomes: access_log /var/log/nginx/luna-backend.access.log;
```

---

## Common Nginx Operations

### Check what's listening on ports
```bash
sudo netstat -tlnp | grep LISTEN
```

### Check Nginx process
```bash
ps aux | grep nginx
```

### View Nginx version
```bash
nginx -v
```

### View Nginx modules
```bash
nginx -V
```

### Restart Nginx (careful — stops then starts)
```bash
sudo systemctl restart nginx
```

### Graceful reload (no connection drop)
```bash
sudo systemctl reload nginx
```

---

## Troubleshooting

### Nginx won't start
1. Check syntax: `sudo nginx -t`
2. View error logs: `tail -f /var/log/nginx/error.log`
3. Check ports: `sudo netstat -tlnp | grep 80`

### SSL certificate issues
1. Check cert exists: `ls /etc/letsencrypt/live/`
2. View cert: `sudo openssl x509 -text -noout -in /etc/letsencrypt/live/{domain}/fullchain.pem`
3. Renew cert: `sudo certbot renew`

### Config file syntax error
```bash
sudo nginx -t  # Shows exact error and line number
sudo nginx -T  # Shows full resolved config
```

---

## Your Current Setup (From Your Message)

You have Nginx files:
```
/etc/nginx/sites-available/ats-frontend.conf   (created Jul 27 14:24)
/etc/nginx/sites-available/pm-frontend.conf    (created Jul 27 15:04)
```

And PM2 app running:
```
luna-backend: online, 76.0MB, fork mode
```

To see luna-backend's Nginx config (if it exists):
```bash
cat /etc/nginx/sites-available/luna-backend.conf
```

Or check if it's enabled:
```bash
ls /etc/nginx/sites-enabled/ | grep luna
```

---

## For Your Next Deployments

**When deploying**: Answer the Nginx question to auto-configure  
**After deploying**: Run `cat /etc/nginx/sites-available/{app-name}.conf` to see what was created  
**To modify**: Edit the file, test with `sudo nginx -t`, then reload

---

**Quick Ref**: All Nginx files are in `/etc/nginx/sites-available/`
