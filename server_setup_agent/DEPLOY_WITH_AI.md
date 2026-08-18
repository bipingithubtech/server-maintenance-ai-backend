# Deploy This Repo Using AI Agent

The **server-maintenance-ai-backend** repository can now be deployed automatically using the AI deployment agent with Docker containerization.

## How It Works

When you ask the AI agent to deploy this repository:

```
"deploy https://github.com/bipingithubtech/server-maintenance-ai-backend.git stack fastapi port 8000"
```

The AI will **automatically**:

1. ✅ Detect it's the `server-maintenance-ai-backend` repo
2. ✅ Set deployment method to **Docker** (instead of PM2/systemd)
3. ✅ Use the existing `Dockerfile` and `docker-compose.yml`
4. ✅ Build a Docker image with all dependencies
5. ✅ Run the container with proper networking and environment variables
6. ✅ Set up Nginx reverse proxy with SSL support (if domain is provided)

## Deployment Steps

### 1. Ask the AI Agent to Deploy

From your client, send:

```json
{
  "query": "deploy https://github.com/bipingithubtech/server-maintenance-ai-backend.git stack fastapi port 8000 domain smai.meetri.in"
}
```

### 2. AI Will Ask for Environment Variables

The AI will ask:
```
Does this app need a .env file? If yes, provide the variables as JSON...
```

Respond with your environment variables:
```json
{
  "GROQ_API_KEY": "gsk_xxxxx",
  "GITHUB_TOKEN": "ghp_xxxxx",
  "TEAMS_WEBHOOK_URL": "https://xxx.c1.environment.api.powerplatform.com..."
}
```

### 3. Deployment Completes

The AI will:
- Clone the repository
- Build the Docker image
- Start containers (app, PostgreSQL, Qdrant)
- Configure Nginx reverse proxy
- Return success status

```
DEPLOYMENT COMPLETE
App:    server-maintenance-ai-backend
URL:    https://smai.meetri.in
Port:   8000
Stack:  fastapi

✓ clone
✓ env_file (5 vars)
✓ install (fastapi via docker)
✓ nginx
```

## What Gets Deployed

The `docker-compose.yml` orchestrates:

```
┌─────────────────────────────────────┐
│   Nginx (Reverse Proxy)             │
│   Port: 80, 443                     │
└────────────────┬────────────────────┘
                 │
    ┌────────────▼────────────┐
    │  FastAPI App            │
    │  Port: 8000             │
    │  Image: smai-app        │
    └────────┬──────┬─────────┘
             │      │
    ┌────────▼──┐   ┌─▼──────────┐
    │ PostgreSQL │   │  Qdrant    │
    │ (Database) │   │  (Vectors) │
    └───────────┘   └────────────┘
```

### Containers

- **smai-app** — FastAPI application (Python Uvicorn)
- **smai-postgres** — PostgreSQL database (Port 5432)
- **smai-qdrant** — Vector database for embeddings (Port 6333)
- **smai-nginx** — Nginx reverse proxy (Port 80/443)

### Data Persistence

- Database: `/var/lib/docker/volumes/postgres_data`
- Vector DB: `/var/lib/docker/volumes/qdrant_data`
- Logs: `./logs/` (mounted from host)

## Monitoring & Maintenance

### View Status

```bash
# SSH into server
ssh user@server_ip

# Check running containers
docker-compose ps

# View app logs (real-time)
docker-compose logs -f app

# View database logs
docker-compose logs -f postgres

# View Nginx logs
docker-compose logs -f nginx
```

### Database Access

```bash
# Access PostgreSQL CLI
docker-compose exec postgres psql -U smai_user -d server_maintenance_db

# Run migrations
docker-compose exec app alembic upgrade head

# Export data
docker-compose exec postgres pg_dump -U smai_user server_maintenance_db > backup.sql
```

### Restart Services

```bash
# Restart single service
docker-compose restart app

# Restart all services
docker-compose restart

# Rebuild and restart
docker-compose up -d --build

# Stop all services
docker-compose down

# Remove containers and volumes
docker-compose down -v
```

## SSL/HTTPS Setup

### With Domain (Automatic HTTPS)

If you provide a domain like `smai.meetri.in`:

1. AI creates SSL certificates automatically
2. Nginx redirects HTTP to HTTPS
3. All traffic encrypted

### Self-Signed Certificates (Development)

```bash
# SSH into server
ssh user@server_ip

# Create certs directory
mkdir -p certs

# Generate self-signed cert (valid 365 days)
openssl req -x509 -newkey rsa:4096 -nodes \
  -out certs/server.crt -keyout certs/server.key -days 365 \
  -subj "/C=US/ST=State/L=City/O=Org/CN=smai.meetri.in"

# Restart Nginx
docker-compose restart nginx
```

### Production Certificates (Let's Encrypt)

```bash
# Install certbot
sudo apt-get install certbot python3-certbot-nginx

# Get certificate
sudo certbot certonly --standalone -d smai.meetri.in

# Copy to certs
sudo cp /etc/letsencrypt/live/smai.meetri.in/fullchain.pem certs/server.crt
sudo cp /etc/letsencrypt/live/smai.meetri.in/privkey.pem certs/server.key

# Fix permissions
sudo chown $USER:$USER certs/*

# Restart Nginx
docker-compose restart nginx
```

## Environment Variables

Required `.env` variables:

```bash
# LLM Configuration
GROQ_API_KEY=gsk_xxxx
MODEL_NAME=openai/gpt-oss-120b
LLM_PROVIDER=groq
TEMPERATURE=0.0

# GitHub (for private repos)
GITHUB_TOKEN=ghp_xxxx

# SSH Configuration (for connecting to servers)
SSH_HOST=127.0.0.1
SSH_PORT=22
SSH_USERNAME=root
SSH_KEY_PATH=/home/user/.ssh/id_ed25519

# Teams Notifications
TEAMS_WEBHOOK_URL=https://xxx.c1.environment.api.powerplatform.com...
```

## Troubleshooting

### Container won't start

```bash
# Check logs
docker-compose logs app

# Common issues:
# 1. Port already in use → Change port in docker-compose.yml
# 2. Missing env vars → Check .env file
# 3. Database connection → Ensure postgres is healthy
```

### Database connection failed

```bash
# Check PostgreSQL is running
docker-compose ps postgres

# Check postgres logs
docker-compose logs postgres

# Connect directly to test
docker-compose exec postgres psql -U smai_user -d server_maintenance_db -c "SELECT 1"
```

### Out of disk space

```bash
# Check usage
docker system df

# Remove unused images/containers
docker system prune -a

# Clean up volumes (WARNING: deletes data)
docker volume prune
```

## AI Deployment Logic

The AI agent automatically:

1. **Detects this repo** by checking for `server-maintenance-ai-backend` in the URL
2. **Sets process_manager to "docker"** instead of asking
3. **Checks for Dockerfile** and uses it
4. **Auto-generates Dockerfile** if missing (uses requirements.txt)
5. **Installs Docker** on the server if needed
6. **Builds image** with tag `server-maintenance-ai-backend`
7. **Runs container** with proper networking and volumes
8. **Configures Nginx** for reverse proxying
9. **Sets up health checks** and restart policies

### Special Cases

**If you want to override Docker deployment:**

```
"deploy https://github.com/bipingithubtech/server-maintenance-ai-backend.git stack fastapi port 8000 using pm2"
```

This tells the AI to use PM2 instead of Docker.

**If you want to use custom Dockerfile:**

```
"deploy https://github.com/bipingithubtech/server-maintenance-ai-backend.git stack fastapi port 8000 dockerfile /custom/path/Dockerfile"
```

## Next Steps

1. ✅ Dockerfile is ready
2. ✅ docker-compose.yml is configured
3. ✅ AI agent will auto-detect and deploy with Docker
4. ✅ Just ask the AI to deploy the repo!

Example:
```
"hey, deploy the server-maintenance-ai-backend to my server with docker"
```

The AI will handle everything else.

## Reference

- **Docker Compose**: `docker-compose.yml`
- **Dockerfile**: `Dockerfile`
- **Nginx Config**: `nginx.conf`
- **Deployment Guide**: `DOCKER_DEPLOY.md`
- **Full Setup**: See README.md for more details
