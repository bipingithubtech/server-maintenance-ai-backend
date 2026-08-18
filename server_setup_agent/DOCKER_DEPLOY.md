# Docker Deployment Guide

This application can be deployed using Docker and Docker Compose. Everything you need is configured.

## Files Overview

- **Dockerfile** — Builds the FastAPI application image
- **docker-compose.yml** — Orchestrates all services (app, PostgreSQL, Qdrant, Nginx)
- **nginx.conf** — Reverse proxy configuration with SSL support
- **.dockerignore** — Excludes unnecessary files from Docker build

## Quick Start

### 1. Set Up Environment Variables

Copy the values from `.env` and update `docker-compose.yml` or create a `.env.docker` file:

```bash
cp .env .env.docker
```

Edit `.env.docker` to add/update:
```
GROQ_API_KEY=your_key_here
GITHUB_TOKEN=your_token_here
TEAMS_WEBHOOK_URL=your_webhook_here
SSH_HOST=your_server_ip
SSH_USERNAME=your_ssh_user
```

### 2. Build and Start Services

```bash
# Start all services (builds image if not exists)
docker-compose up -d

# Or with custom env file
docker-compose --env-file .env.docker up -d

# View logs
docker-compose logs -f app

# View specific service logs
docker-compose logs -f postgres
docker-compose logs -f qdrant
```

### 3. Verify Services Are Running

```bash
# Check all containers
docker-compose ps

# Test the API
curl http://localhost:8000/

# Expected response:
# {"status":"ok","message":"AI Server Setup Agent API is running!"}
```

### 4. Access Services

- **FastAPI API**: http://localhost:8000
- **Swagger Docs**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **PostgreSQL**: localhost:5432 (credentials in docker-compose.yml)
- **Qdrant UI**: http://localhost:6333/dashboard (optional)
- **Nginx**: http://localhost:80 (redirects to https, requires cert setup)

## Database Setup

The PostgreSQL database is automatically initialized. To run migrations:

```bash
# Access the container
docker-compose exec app bash

# Run alembic migrations
alembic upgrade head

# Exit
exit
```

## SSL/TLS Setup

Nginx is configured for HTTPS but needs certificates. Two options:

### Option 1: Self-Signed Certificates (Dev/Testing)

```bash
# Create certs directory
mkdir -p certs

# Generate self-signed certificate (valid for 365 days)
openssl req -x509 -newkey rsa:4096 -nodes \
  -out certs/server.crt -keyout certs/server.key -days 365 \
  -subj "/C=US/ST=State/L=City/O=Org/CN=localhost"

# Uncomment nginx service in docker-compose.yml if needed
docker-compose up -d nginx
```

### Option 2: Let's Encrypt (Production)

```bash
# Use certbot to obtain certificates
certbot certonly --standalone -d yourdomain.com

# Copy certificates to certs directory
cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem certs/server.crt
cp /etc/letsencrypt/live/yourdomain.com/privkey.pem certs/server.key

# Start nginx
docker-compose up -d nginx
```

## Common Commands

```bash
# Start services
docker-compose up -d

# Stop services
docker-compose down

# Remove all containers and volumes
docker-compose down -v

# Rebuild image
docker-compose build --no-cache

# Rebuild and restart
docker-compose up -d --build

# View real-time logs
docker-compose logs -f

# Execute command in app container
docker-compose exec app python -c "import sys; print(sys.version)"

# Bash shell in app container
docker-compose exec app bash

# Scale services (if applicable)
docker-compose up -d --scale worker=3
```

## Environment Variables Reference

```
# LLM Configuration
LLM_PROVIDER=groq|openai
GROQ_API_KEY=your_key
MODEL_NAME=model_name
TEMPERATURE=0.0

# GitHub
GITHUB_TOKEN=ghp_xxxx

# SSH (for connecting to servers)
SSH_HOST=server_ip
SSH_PORT=22
SSH_USERNAME=username
SSH_KEY_PATH=/app/.ssh/id_ed25519

# Database
DATABASE_URL=postgresql://user:pass@host:5432/db

# Vector DB
QDRANT_URL=http://qdrant:6333
QDRANT_API_KEY=key

# Notifications
TEAMS_WEBHOOK_URL=webhook_url
```

## Troubleshooting

### Container exits immediately
```bash
docker-compose logs app
# Check the error message and fix accordingly
```

### Port already in use
```bash
# Change ports in docker-compose.yml
# Example: change "8000:8000" to "8001:8000"
```

### Database connection failed
```bash
# Ensure postgres service is healthy
docker-compose ps

# Check postgres logs
docker-compose logs postgres

# Verify DATABASE_URL in docker-compose.yml
```

### Permission denied on SSH key
```bash
# Ensure SSH key has correct permissions
chmod 600 ~/.ssh/id_ed25519

# Verify path in docker-compose.yml volumes
```

## Production Deployment

For production on a remote server:

1. **Update docker-compose.yml**:
   - Use `.env` file for secrets
   - Set appropriate database credentials
   - Configure HTTPS certificates
   - Set resource limits

2. **Use environment file**:
   ```bash
   docker-compose --env-file /path/to/.env.production up -d
   ```

3. **Enable auto-restart**:
   ```bash
   docker update --restart unless-stopped smai-app
   docker update --restart unless-stopped smai-postgres
   ```

4. **Monitoring**:
   ```bash
   docker stats
   docker-compose logs --tail 100 app
   ```

## Architecture

```
┌─────────────────────────────────────────┐
│          Client/Browser                 │
└────────────────┬────────────────────────┘
                 │ HTTPS (443)
┌────────────────▼────────────────────────┐
│         Nginx Reverse Proxy             │
│         (SSL/TLS Termination)           │
└────────────────┬────────────────────────┘
                 │ HTTP (8000)
┌────────────────▼────────────────────────┐
│    FastAPI App (Uvicorn)                │
│    - Chat API                           │
│    - Deployment Agents                  │
│    - Server Management                  │
└────────────┬──────────────┬─────────────┘
             │ TCP:5432     │ HTTP:6333
    ┌────────▼──────┐   ┌───▼──────────┐
    │  PostgreSQL   │   │   Qdrant     │
    │  (Database)   │   │   (Vectors)  │
    └───────────────┘   └──────────────┘
```

## Next Steps

1. Deploy to Docker
2. Test with `curl http://localhost:8000/`
3. Configure your servers in the application
4. Start using the AI agents for deployment

For more details, see README.md
