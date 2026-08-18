# AI-Powered Deployment System

This backend now includes an intelligent AI deployment system that can deploy applications (including this repo itself) using Docker, PM2, or systemd.

## Quick Overview

### What is Deployed

| Repo | Stack | Default Method | Port | Domain |
|------|-------|-----------------|------|--------|
| server-maintenance-ai-backend | FastAPI | Docker | 8000 | configurable |
| Any Node.js app | Node/Next.js | PM2 | 3000+ | configurable |
| Any Python app | Flask/Django | Systemd | 5000+ | configurable |
| Any React app | React/Vite | PM2 | 3000 | configurable |

## How to Use

### 1. Deploy This Repo (Docker)

```bash
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "deploy https://github.com/bipingithubtech/server-maintenance-ai-backend.git stack fastapi port 8000 domain api.example.com"
  }'
```

**Result**: Docker containers for FastAPI, PostgreSQL, Qdrant, and Nginx

### 2. Deploy Another App (PM2)

```bash
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "deploy https://github.com/org/app.git stack nextjs port 3000 domain app.example.com"
  }'
```

**Result**: Next.js app running on PM2

### 3. Deploy with Custom Environment

```bash
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "deploy https://github.com/org/api.git stack fastapi port 8000 env DATABASE_URL=postgres://localhost/db SECRET_KEY=abc123"
  }'
```

## Deployment Flow

```
User Request
    ↓
AI Gathers Context (LLM)
    ├─ Extract: github_url, stack, port, domain
    ├─ Detect: process_manager (docker, pm2, systemd)
    ├─ Check: git branch availability
    ├─ Validate: GitHub token for private repos
    └─ Collect: environment variables
    ↓
Deployment Steps
    ├─ Step 1: Clone repository
    ├─ Step 2: Write .env file
    ├─ Step 3: Install & Start (docker/pm2/systemd)
    ├─ Step 4: Configure Nginx reverse proxy
    └─ Step 5: Health check & report
    ↓
Success Response
    └─ URL, port, status
```

## AI Deployment Strategies

### For FastAPI (this repo)

```
detection: "server-maintenance-ai-backend" in URL
→ process_manager: docker (automatic)
→ Build Dockerfile if missing
→ Run docker-compose up
→ Auto-scale: PostgreSQL + Qdrant + Nginx
```

### For Node.js Apps

```
detection: package.json with "next" | "nest" | "react"
→ process_manager: pm2 (default)
→ npm install & npm run build
→ pm2 start
→ Configure Nginx reverse proxy
```

### For Python Apps (Django/Flask)

```
detection: requirements.txt found
→ process_manager: systemd (default)
→ Create venv
→ pip install
→ Create systemd service
→ systemctl enable & start
```

## Deployment Configuration

### Automatic Detection

The AI automatically detects:

| Detection | Action |
|-----------|--------|
| "docker" in query | Use Docker |
| "pm2" in query | Use PM2 |
| "systemd" in query | Use Systemd |
| FastAPI + server-maintenance-ai-backend | Use Docker |
| Node.js repo | Use PM2 |
| Python repo | Use Systemd |

### Manual Override

```
"deploy [url] using docker"
"deploy [url] using pm2"
"deploy [url] using systemd"
```

## Files Involved

| File | Purpose |
|------|---------|
| `deployment_agent.py` | Main deployment orchestration |
| `Dockerfile` | Docker build config |
| `docker-compose.yml` | Multi-container orchestration |
| `nginx.conf` | Reverse proxy configuration |
| `.env` | Environment variables |
| `requirements.txt` | Python dependencies |

## AI Agent Capabilities

### Gather Phase
- ✅ Extract deployment parameters from natural language
- ✅ Detect stack type automatically
- ✅ Validate GitHub URLs
- ✅ Check for private repos
- ✅ Suggest process managers

### Clone Phase
- ✅ Authenticate with GitHub token
- ✅ Clone specific branch
- ✅ Handle existing repos (git pull vs fresh clone)
- ✅ Manage directory permissions

### Install Phase
- ✅ Detect dependencies (requirements.txt, package.json)
- ✅ Auto-generate Dockerfile if missing
- ✅ Install Docker/Node/Python as needed
- ✅ Build images or install packages

### Start Phase
- ✅ Start Docker containers
- ✅ Start PM2 process manager
- ✅ Create systemd services
- ✅ Detect actual listening port
- ✅ Health check

### Nginx Phase
- ✅ Configure reverse proxy
- ✅ Set up SSL/TLS certificates
- ✅ Configure domain/IP routing
- ✅ Handle WebSocket upgrades

## Environment Variables

The AI can accept environment variables in multiple formats:

```
"env DATABASE_URL=postgres://localhost/db SECRET=abc123"

or

"env {\"DATABASE_URL\": \"postgres://localhost/db\", \"SECRET\": \"abc123\"}"
```

The AI will:
1. Parse the environment variables
2. Write them to .env file on server
3. Load them in Docker/process manager
4. Keep them secure (not in logs)

## Error Handling

### Clone Failures
- GitHub token invalid → Ask for new token
- Repository not found → Verify URL
- Permission denied → Check token scopes

### Installation Failures
- Disk full → Suggest cleanup
- Missing dependencies → Auto-install
- Port conflict → Suggest alternative port

### Startup Failures
- Health check timeout → Increase timeout
- Port binding error → Kill previous process
- Memory insufficient → Suggest resource increase

## Security Features

✅ **Credentials Management**
- GitHub tokens loaded from .env
- SSH keys mounted read-only
- Secrets not logged

✅ **Process Isolation**
- Docker containers isolated
- Non-root user in containers
- Network segmentation

✅ **SSL/TLS**
- HTTPS-only Nginx
- Automatic certificate generation
- Security headers configured

## Monitoring & Management

### View Deployment Status

```bash
# Check if app is running
curl http://server:8000/

# View logs
docker-compose logs app          # Docker
pm2 logs app-name               # PM2
journalctl -u app-name          # Systemd
```

### Stop/Restart

```
"stop server-maintenance-ai-backend"
"restart server-maintenance-ai-backend"
"redeploy server-maintenance-ai-backend"
```

### Check Ports

```
"what port is my-app running on?"
"which app is listening on port 3000?"
```

## Examples

### Deploy Flask API

```
"deploy https://github.com/org/flask-api.git stack flask port 5000 domain api.example.com"
```

### Deploy React Frontend

```
"deploy https://github.com/org/react-app.git stack react port 3000 domain app.example.com"
```

### Deploy NestJS Backend

```
"deploy https://github.com/org/nestjs-app.git stack nestjs port 3001 domain api.example.com"
```

### Deploy This Repo

```
"deploy https://github.com/bipingithubtech/server-maintenance-ai-backend.git stack fastapi port 8000"
```

The AI will automatically use Docker for this repo.

## Advanced Features

### Multi-App Deployment

Deploy multiple apps to the same server:

```
"deploy app1 to /home/apps/app1"
"deploy app2 to /home/apps/app2"
"deploy app3 to /home/apps/app3"
```

AI will:
- Clone each to separate subdirectory
- Start each with unique port
- Configure Nginx routes for each

### Environment-Specific Deployment

```
"deploy my-app with env NODE_ENV=production LOG_LEVEL=error"
```

### Port-Specific Deployment

```
"deploy my-app on port 9000"
```

AI will:
- Check if port is free
- Suggest alternatives if occupied
- Bind app to specific port

## Troubleshooting

### Deployment Stuck

If AI is waiting for input, provide the information:

```
- "What port?" → "3000"
- "Which branch?" → "main" or "develop"
- "Process manager?" → "docker" or "pm2"
- "Environment vars?" → "no" or JSON object
```

### Docker Issues

```bash
# Rebuild image
docker-compose build --no-cache

# Check container status
docker-compose ps

# View logs
docker-compose logs -f
```

### Port Conflicts

AI will either:
1. Auto-resolve if it's the same app (kill & restart)
2. Suggest alternative port if it's a different process
3. Ask you to choose

### GitHub Token Invalid

The AI will:
1. Detect authentication failure
2. Ask for new token
3. Test token validity
4. Retry clone

## Next Steps

1. ✅ This repo has Docker deployment ready
2. ✅ AI agent auto-detects Docker deployment
3. ✅ Just ask: "deploy server-maintenance-ai-backend"
4. ✅ Everything else is automatic!

## See Also

- `DEPLOY_WITH_AI.md` — Detailed deployment guide for this repo
- `DOCKER_DEPLOY.md` — Docker-specific deployment details
- `deployment_agent.py` — Implementation details
