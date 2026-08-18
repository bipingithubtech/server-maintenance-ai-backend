---
inclusion: manual
---

# Configure Nginx & SSL Certificate for Deployed Apps

When the deployment agent finishes, your app is running but may not have Nginx or SSL configured yet.
Use the ops agent to configure these later — this keeps initial deployment fast.

## What You Can Do

### 1. Configure Nginx (HTTP-only or HTTPS)

Use the **nginx_setup** tool to set up or update Nginx configuration for any app.

**When to use:**
- App was deployed but Nginx was skipped
- App is running on a direct port (e.g., localhost:5173) and you want to proxy it through Nginx
- You want to change the domain or port configuration
- App type is frontend (React/Vite) or backend (Node/Python)

**Example prompts:**
- "Configure Nginx for server-maintenance-ai frontend on deploy.meetri.in"
- "Set up Nginx reverse proxy for luna-backend on pm.meetri.in pointing to port 3000"
- "Nginx setup for the React app at /home/meetri/ui/server-maintenance-ai/server-ai"

**Required information:**
- App name (e.g., "server-maintenance-ai")
- Full app path (e.g., "/home/meetri/ui/server-maintenance-ai/server-ai")
- Port the app listens on (e.g., "5173" for Vite)
- Domain or IP (e.g., "deploy.meetri.in" or "31.97.224.45")
- App type: "frontend" (static files) or "backend" (running process)

### 2. Configure SSL/HTTPS with Let's Encrypt

Use the **configure_ssl** tool to request and install an SSL certificate.

**When to use:**
- You've configured Nginx with a domain name and want to enable HTTPS
- You need to renew an existing certificate
- Nginx is already set up on deploy.meetri.in and you want to add HTTPS

**Prerequisites:**
- Domain must be pointing to your server's IP (DNS propagation required)
- Port 80 and 443 must be accessible from the internet
- Nginx must already be configured for the domain

**Example prompts:**
- "Configure SSL certificate for deploy.meetri.in"
- "Set up Let's Encrypt for pm.meetri.in using admin@meetri.in"
- "Enable HTTPS for the deployment domain"

**Required information:**
- Domain (e.g., "deploy.meetri.in")
- Email for Let's Encrypt (e.g., "admin@meetri.in")

## Typical Workflow

1. **Deploy app:** 
   ```
   Deploy https://github.com/bipingithubtech/server-maintenance-ai.git to port 5173
   ```
   → App is running on localhost:5173

2. **Configure Nginx:**
   ```
   Configure Nginx for server-maintenance-ai frontend on deploy.meetri.in
   ```
   → App is now accessible at http://deploy.meetri.in

3. **Configure SSL:**
   ```
   Set up SSL certificate for deploy.meetri.in with email admin@meetri.in
   ```
   → App is now accessible at https://deploy.meetri.in (auto-redirects from HTTP)

## Important Notes

- **SSL requires DNS to be set up first** - Your domain must point to your server before requesting a certificate
- **Initial deployment skips SSL** - This keeps deployments fast and reliable (no terminal/sudo issues)
- **You can configure Nginx/SSL anytime** - Even days/weeks after deployment
- **Auto-renewal** - Let's Encrypt certificates auto-renew 30 days before expiration
- **Rate limiting** - Let's Encrypt has rate limits. If you see errors about "too many failed authorizations", wait 1 hour before retrying

## Troubleshooting

**"Too many failed authorizations" error:**
- Domain DNS not pointing to your server
- Port 80 not accessible from internet
- Wait 1 hour before retrying (Let's Encrypt rate limit)

**"Connection timed out" for domain:**
- DNS not propagated yet (can take 15-30 minutes)
- Firewall blocking port 80/443

**Want to check DNS propagation:**
- Ask the ops agent: "Check DNS resolution for deploy.meetri.in"
