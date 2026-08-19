# SSH Key Setup for Deployment Server

## Problem
The remote server (31.97.224.45:2200) only accepts **publickey authentication** and rejects password authentication.

Error: `Bad authentication type; allowed types: ['publickey']`

## Solution: Setup SSH Keys in Docker Container

### Step 1: Generate SSH Key Pair on Your Deployment Server

SSH into your deployment server and generate a key pair:

```bash
# Generate Ed25519 key (recommended - more secure and faster than RSA)
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_meetri -C "meetri-deployment-bot"

# Or generate RSA key (if Ed25519 is not supported)
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa_meetri -C "meetri-deployment-bot"
```

When prompted:
- Enter file location: Press Enter (use default) or specify custom path
- Enter passphrase: **Leave empty** (press Enter twice) - container needs passwordless access

### Step 2: Copy Public Key to Target Server

Copy the **public key** to the server you want to connect to:

```bash
# Copy public key to target server
ssh-copy-id -i ~/.ssh/id_ed25519_meetri.pub meetri@31.97.224.45 -p 2200

# Or manually append it:
cat ~/.ssh/id_ed25519_meetri.pub | ssh meetri@31.97.224.45 -p 2200 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

### Step 3: Test SSH Key Authentication

Verify the key works:

```bash
ssh -i ~/.ssh/id_ed25519_meetri meetri@31.97.224.45 -p 2200
```

You should login **without being asked for a password**.

### Step 4: Update Docker Configuration

#### A. Update `.env` file:

```env
# SSH Key Path (on host machine)
SSH_KEY_PATH=/root/.ssh/id_ed25519_meetri
```

#### B. Update `docker-compose.yml`:

Uncomment and modify the SSH key volume mount:

```yaml
volumes:
  - ./logs:/app/logs
  - ./configs:/app/configs
  # Mount SSH key (read-only)
  - ~/.ssh/id_ed25519_meetri:/app/.ssh/id_ed25519:ro
```

### Step 5: Update API Request Format

When connecting from frontend, use `key_filename` instead of `password`:

```json
{
  "host": "31.97.224.45",
  "port": 2200,
  "username": "meetri",
  "key_filename": "/app/.ssh/id_ed25519"
}
```

### Step 6: Restart Docker Container

```bash
docker-compose down
docker-compose up -d
```

---

## Alternative: Embed SSH Key in Docker Image (Not Recommended for Security)

If you want the key baked into the image (less secure):

1. Copy private key to project directory
2. Add to Dockerfile:
```dockerfile
COPY id_ed25519_meetri /app/.ssh/id_ed25519
RUN chmod 600 /app/.ssh/id_ed25519
```

**⚠️ Warning:** Never commit private keys to git!

---

## Why It Works Locally

Your local Windows machine has SSH keys in `C:\Users\Bipin Joshi\.ssh\` that paramiko automatically finds and uses via `look_for_keys=True`.

The Docker container doesn't have these keys, so it falls back to password authentication, which the server rejects.

---

## Security Best Practices

1. ✅ Use Ed25519 keys (more secure than RSA)
2. ✅ Mount keys as read-only (`:ro`)
3. ✅ Use passwordless keys for automation
4. ✅ Never commit private keys to version control
5. ✅ Add `.ssh/` to `.gitignore`
6. ✅ Use different keys for different purposes
7. ✅ Rotate keys regularly

---

## Troubleshooting

### Still getting "publickey" error?

Check target server's SSH config (`/etc/ssh/sshd_config`):
```bash
PubkeyAuthentication yes
PasswordAuthentication no
```

### Permission denied?

Check key permissions:
```bash
chmod 600 ~/.ssh/id_ed25519_meetri
chmod 644 ~/.ssh/id_ed25519_meetri.pub
```

### Key not found in container?

Check if volume is mounted:
```bash
docker exec -it smai-app ls -la /app/.ssh/
```
