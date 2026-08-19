# 🔑 Quick Fix: SSH Key Authentication Issue

## The Problem

**Error:** `Bad authentication type; allowed types: ['publickey']`

**Why:** The server `31.97.224.45:2200` **only accepts SSH key authentication** and rejects passwords.

**Why it works locally:** Your Windows machine has SSH keys in `C:\Users\Bipin Joshi\.ssh\` that are automatically used.

**Why it fails on deployment server:** The Docker container doesn't have these SSH keys.

---

## Quick Fix (3 Steps)

### Step 1: Generate SSH Key on Deployment Server

SSH into your deployment server and run:

```bash
# Generate key (passwordless for automation)
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_meetri -N ""

# Copy public key to target server (enter password when prompted)
ssh-copy-id -i ~/.ssh/id_ed25519_meetri.pub meetri@31.97.224.45 -p 2200

# Test it works
ssh -i ~/.ssh/id_ed25519_meetri meetri@31.97.224.45 -p 2200
```

### Step 2: Mount SSH Key in Docker

Edit `docker-compose.yml`, find the `volumes:` section and add:

```yaml
volumes:
  - ./logs:/app/logs
  - ./configs:/app/configs
  # Add this line:
  - ~/.ssh/id_ed25519_meetri:/app/.ssh/id_ed25519:ro
```

### Step 3: Restart Container

```bash
docker-compose down
docker-compose up -d
```

---

## Testing from Frontend

Update your connection request to use SSH key instead of password:

### ❌ Old (Password - Won't Work):
```json
{
  "host": "31.97.224.45",
  "port": 2200,
  "username": "meetri",
  "password": "Meetri@12345"
}
```

### ✅ New (SSH Key - Will Work):
```json
{
  "host": "31.97.224.45",
  "port": 2200,
  "username": "meetri",
  "key_filename": "/app/.ssh/id_ed25519"
}
```

---

## Automated Setup

Run the setup script:

```bash
chmod +x setup_ssh_key.sh
./setup_ssh_key.sh
```

This automates all 3 steps above.

---

## Verification

After setup, check the logs. You should see:

```
[SSH] Connecting to meetri@31.97.224.45:2200 using key authentication
[SSH] Using key file: /app/.ssh/id_ed25519
[SSH] ✓ Connected successfully to 31.97.224.45
[API] ✓ Connected as meetri
```

---

## Security Notes

- ✅ Private key stays on deployment server only
- ✅ Mounted as read-only (`:ro`) in container
- ✅ Never commit private keys to git
- ✅ Public key is copied to target server's `~/.ssh/authorized_keys`

---

## Need Help?

See detailed guide: `SETUP_SSH_KEYS.md`
