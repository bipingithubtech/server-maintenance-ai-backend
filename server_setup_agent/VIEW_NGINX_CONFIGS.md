# View Your Nginx Configs — Quick Commands

## Your Server Status

**Current state** (from your terminal output):
- ✅ luna-backend running on PM2 (76MB, online)
- ✅ ats-frontend.conf created in Nginx
- ✅ pm-frontend.conf created in Nginx

---

## See All Your Nginx Configs

On your server, run:

```bash
ls -la /etc/nginx/sites-available/
```

**What you'll see**:
```
-rw-r--r-- 1 root root 4967 Jul 27 14:24 ats-frontend.conf
-rw-r--r-- 1 root root 2155 Jul 27 15:04 pm-frontend.conf
-rw-r--r-- 1 root root XXXX Jul 27 ??:?? luna-backend.conf  (if created)
```

---

## View Specific Nginx Config File

To see the Nginx config for **luna-backend**:

```bash
cat /etc/nginx/sites-available/luna-backend.conf
```

**Output will show**:
- Domain/IP it's listening on
- Port it proxies to (probably 3000)
- SSL settings (if domain used)
- Rate limiting
- Security headers
- Logging paths

---

## View All Nginx Logs

**Nginx access logs for luna-backend**:
```bash
tail -50 /var/log/nginx/luna-backend.access.log
```

**Nginx error logs for luna-backend**:
```bash
tail -50 /var/log/nginx/luna-backend.error.log
```

**All requests**:
```bash
tail -f /var/log/nginx/access.log
```

---

## Verify Nginx is Working

Check if Nginx is running:
```bash
sudo systemctl status nginx
```

Should show: `Active: active (running)`

---

## Check Which Sites Are Enabled

Nginx configs in `sites-available/` only work if they're **enabled** (symlinked in `sites-enabled/`):

```bash
ls /etc/nginx/sites-enabled/
```

**If luna-backend.conf is not there**, enable it:
```bash
sudo ln -s /etc/nginx/sites-available/luna-backend.conf /etc/nginx/sites-enabled/
sudo systemctl reload nginx
```

---

## Simple View (All at Once)

See all Nginx configs + which are enabled:

```bash
echo "=== AVAILABLE ===" && ls /etc/nginx/sites-available/ && echo -e "\n=== ENABLED ===" && ls /etc/nginx/sites-enabled/
```

---

## Full Content of Each Config

**View ats-frontend**:
```bash
cat /etc/nginx/sites-available/ats-frontend.conf
```

**View pm-frontend**:
```bash
cat /etc/nginx/sites-available/pm-frontend.conf
```

**View luna-backend**:
```bash
cat /etc/nginx/sites-available/luna-backend.conf
```

---

## View Specific Section

Find just the domain name in luna-backend config:
```bash
grep "server_name" /etc/nginx/sites-available/luna-backend.conf
```

Find just the port proxy:
```bash
grep "proxy_pass" /etc/nginx/sites-available/luna-backend.conf
```

---

## Export to File (For Reference)

Copy Nginx config to your local machine (if needed):

```bash
# On server:
cat /etc/nginx/sites-available/luna-backend.conf > ~/luna-backend-nginx.conf

# Then copy to local:
scp joshi@aiagent:~/luna-backend-nginx.conf ./
```

---

## What Each Config File Contains

### Structure:
```nginx
# Rate limiting zone
limit_req_zone ...

# Map for WebSocket
map $http_upgrade ...

# HTTP redirect to HTTPS
server {
    listen 80;
    return 301 https://...;
}

# HTTPS server (your actual app)
server {
    listen 443 ssl http2;
    server_name your-domain.com;
    
    # SSL certificates
    ssl_certificate ...
    ssl_certificate_key ...
    
    # Security headers
    add_header ...
    
    # Proxying to your app
    location / {
        proxy_pass http://127.0.0.1:PORT;
        ...
    }
    
    # WebSocket support
    location /ws {
        ...
    }
}
```

---

## Integration with Your Deployment

When you deployed, the system:
1. ✅ Asked "Do you want Nginx configured?" 
2. ✅ You said "yes"
3. ✅ Generated config file from template
4. ✅ Saved to `/etc/nginx/sites-available/{app-name}.conf`
5. ✅ Enabled it (created symlink in sites-enabled/)
6. ✅ Tested config (`sudo nginx -t`)
7. ✅ Reloaded Nginx

**Result**: Your app is now accessible via domain/IP, not just `:3000`

---

## For Next Deployments

**If you want to skip Nginx**: Say `no` when asked during deployment
**If you want to add it later**: Tell AI "setup nginx for {app-name}"

---

## Your Current Architecture

```
Internet (HTTPS)
    ↓
Nginx (port 443)
    ↓
Your App (localhost:PORT via PM2)
```

Nginx:
- Handles HTTPS/SSL
- Rate limiting (20 req/sec)
- Caches static files
- Logs all requests
- Serves security headers

Your App (PM2):
- Only listens on localhost (safe)
- Doesn't need to know about domains
- Can restart without Nginx restarting

---

## Quick Command Summary

```bash
# View all available configs
ls /etc/nginx/sites-available/

# View specific config
cat /etc/nginx/sites-available/luna-backend.conf

# Check if enabled
ls /etc/nginx/sites-enabled/

# Enable a site
sudo ln -s /etc/nginx/sites-available/luna-backend.conf /etc/nginx/sites-enabled/

# Test config
sudo nginx -t

# Reload Nginx
sudo systemctl reload nginx

# View access log
tail -50 /var/log/nginx/luna-backend.access.log

# View error log
tail -50 /var/log/nginx/luna-backend.error.log

# Check status
sudo systemctl status nginx
```

---

**Your files are in**: `/etc/nginx/sites-available/`
