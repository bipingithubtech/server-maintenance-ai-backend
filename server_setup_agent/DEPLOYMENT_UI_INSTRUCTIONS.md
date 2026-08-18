# Deployment UI Instructions

## How to Deploy Apps from the Frontend

### Step 1: Connect to Server

When connecting to your server:
- **Host**: Your server IP (e.g., `31.97.224.45`)
- **Username**: `meetri`
- **Password**: Leave EMPTY or your SSH password (only if you use password auth, not key-based auth)
- **SSH Key**: Your private key file (if using key-based auth)
- **Sudo Password**: **LEAVE EMPTY** ← This is the key!

**Why leave sudo password empty?**
Because you have passwordless sudo configured:
```bash
# On your server
sudo visudo
# Add this line:
meetri ALL=(ALL) NOPASSWD: ALL
```

When sudo password is empty, the system runs `sudo` commands directly without trying to inject a password, which works perfectly with passwordless sudo.

### Step 2: Deploy an App

Use this format in the chat:

```
deploy <GITHUB_URL> stack <STACK> port <PORT>
```

**Examples:**

**Vite React App:**
```
deploy https://github.com/bipingithubtech/server-maintenance-ai.git stack vite port 5173
```

**Next.js App:**
```
deploy https://github.com/org/nextjs-app.git stack nextjs port 3000
```

**NestJS Backend:**
```
deploy https://github.com/org/nestjs-api.git stack nestjs port 3001
```

**FastAPI Backend:**
```
deploy https://github.com/org/fastapi-api.git stack fastapi port 8000
```

**Python Django App:**
```
deploy https://github.com/org/django-app.git stack django port 8000
```

### Step 3: Answer Prompts

The AI will ask:

1. **Deployment Location**: Use a base path like `/home/meetri/ui` for multiple apps
   - Single app: `/home/meetri/ui/app-name` → creates flat structure
   - Multiple apps: `/home/meetri/ui` → creates `/home/meetri/ui/app-name/`

2. **Process Manager**: 
   - `pm2` (recommended for Node.js apps)
   - `systemd` (recommended for Python apps)
   - `docker` (for containerized apps)

3. **Git Branch**: Usually `main` or `master`

4. **Environment Variables**:
   - If no .env needed: `no`
   - If .env needed: `{"DATABASE_URL": "postgres://localhost/db", "API_KEY": "secret"}`

5. **Nginx Configuration**:
   - `yes` to set up reverse proxy with domain
   - `no` to skip (app accessible on direct port)
   - Or provide custom domain: `deploy.meetri.in`

## Expected Flow

```
Deployment Complete ✓
├─ Git clone
├─ Install dependencies
├─ Build application
├─ Start process manager (PM2/systemd/Docker)
├─ Configure Nginx (if requested)
└─ HTTPS ready (if domain provided)
```

## Troubleshooting

### "Still asking for sudo password"
- **Check**: Are you leaving the "Sudo Password" field EMPTY when connecting?
- **Solution**: Don't send any sudo_password in credentials

### "Sudo command failed"
- **Check**: Is passwordless sudo configured? Run on server: `sudo echo "test"`
- **Should see**: `test` (no password prompt)
- **If you see password prompt**: Configure it with:
  ```bash
  sudo visudo
  # Add at bottom:
  meetri ALL=(ALL) NOPASSWD: ALL
  ```

### "Command timed out"
- Large npm/pip installs can take time
- Try splitting deployment into:
  1. Clone repo
  2. Install separately
  3. Start app

### "Port already in use"
- The AI will automatically detect conflicts
- Use a different port or:
  ```bash
  sudo lsof -i :3000  # Find what's using port 3000
  sudo kill -9 <PID>  # Kill the process
  ```

## Quick Reference: Supported Stacks

| Stack | Type | Default PM | Default Port |
|-------|------|-----------|--------------|
| `vite` | Frontend | PM2 | 5173 |
| `react` | Frontend | PM2 | 3000 |
| `angular` | Frontend | PM2 | 4200 |
| `nextjs` | Fullstack | PM2 | 3000 |
| `nodejs` | Backend | PM2 | 3000 |
| `nestjs` | Backend | PM2 | 3001 |
| `fastapi` | Backend | Docker | 8000 |
| `flask` | Backend | Systemd | 5000 |
| `django` | Backend | Systemd | 8000 |

## Multi-App Deployment Example

Deploy multiple apps to the same server:

**App 1 - Frontend (Vite):**
```
deploy https://github.com/org/ui.git stack vite port 5173
Base path: /home/meetri/apps
→ Creates: /home/meetri/apps/ui/
```

**App 2 - API (NestJS):**
```
deploy https://github.com/org/api.git stack nestjs port 3001
Base path: /home/meetri/apps
→ Creates: /home/meetri/apps/api/
```

**Result:**
- UI available at `http://server:5173` (and via domain if configured)
- API available at `http://server:3001` (and via domain if configured)
- Nginx routes both apps if domains configured

## Security Notes

✅ **Best Practices:**
- Use SSH key authentication (no password needed for login)
- Configure passwordless sudo for deployment automation
- Use different ports for different apps
- Set up Nginx reverse proxy with HTTPS
- Configure UFW firewall to allow only needed ports

❌ **Avoid:**
- Leaving passwords in chat/logs
- Running as root when not necessary
- Opening all ports in firewall
- Using HTTP for production

## Questions?

- Check deployment logs: `pm2 logs app-name` or `docker-compose logs`
- Monitor app: `pm2 status` or `systemctl status app-name`
- View Nginx configs: `/etc/nginx/sites-available/`
