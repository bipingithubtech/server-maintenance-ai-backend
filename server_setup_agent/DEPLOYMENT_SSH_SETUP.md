# 🚀 Deployment Server SSH Setup

## Problem

Your application works **locally on Windows** but fails on the **deployment server** with:

```
[SSH] No SSH keys found in /home/appuser/.ssh
Authentication failed: Server only accepts SSH key authentication
```

## Root Cause

The deployment server doesn't have SSH keys configured for the user running Docker.

---

## Solution: Setup SSH Keys on Deployment Server

### Step 1: SSH into Deployment Server

```bash
ssh your-user@your-deployment-server
```

### Step 2: Check if SSH Keys Exist

```bash
ls -la ~/.ssh/
```

**If you see `id_ed25519` or `id_rsa`:** Skip to Step 4  
**If directory is empty or doesn't exist:** Continue to Step 3

### Step 3: Generate SSH Keys

```bash
# Generate Ed25519 key (recommended)
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""

# Or generate RSA key (if Ed25519 not supported)
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa -N ""
```

**Output:**
```
Generating public/private ed25519 key pair.
Your identification has been saved in /home/user/.ssh/id_ed25519
Your public key has been saved in /home/user/.ssh/id_ed25519.pub
```

### Step 4: Copy Public Key to Target Server

```bash
# Copy public key to the server you want to manage (31.97.224.45)
ssh-copy-id -i ~/.ssh/id_ed25519.pub meetri@31.97.224.45 -p 2200

# You'll be prompted for password: Meetri@12345
```

**Expected Output:**
```
Number of key(s) added: 1

Now try logging into the machine with:
   "ssh -p 2200 'meetri@31.97.224.45'"
```

### Step 5: Test SSH Connection

```bash
# Test that SSH key authentication works
ssh -i ~/.ssh/id_ed25519 meetri@31.97.224.45 -p 2200 "whoami"
```

**Expected Output:**
```
meetri
```

If you can login **without entering a password**, SSH key auth is working! ✅

### Step 6: Restart Docker Container

```bash
cd /path/to/server_setup_agent
docker-compose restart
```

### Step 7: Test Application

```bash
# Test the connection endpoint
curl -X POST http://localhost:8000/api/v1/connect \
  -H "Content-Type: application/json" \
  -d '{"host":"31.97.224.45","port":2200,"username":"meetri"}'
```

**Expected Response:**
```json
{
  "connected": true,
  "user": "meetri",
  "host": "31.97.224.45"
}
```

---

## Environment Comparison

### Local Windows (Working):

```
User: Bipin Joshi
SSH Keys Location: C:\Users\Bipin Joshi\.ssh\
Docker Mount: ${USERPROFILE}/.ssh → /home/appuser/.ssh
Status: ✅ Keys exist, connection works
```

### Deployment Server (Needs Setup):

```
User: [deployment-user]
SSH Keys Location: /home/[deployment-user]/.ssh/
Docker Mount: ${HOME}/.ssh → /home/appuser/.ssh
Status: ❌ Keys don't exist (until you create them)
```

---

## Troubleshooting

### Issue 1: "No SSH keys found"

**Check if keys exist:**
```bash
ls -la ~/.ssh/id_*
```

**If empty:** Run Step 3 (generate keys)

### Issue 2: "Permission denied (publickey)"

**Problem:** Public key not on target server

**Solution:**
```bash
# Re-copy public key
ssh-copy-id -i ~/.ssh/id_ed25519.pub meetri@31.97.224.45 -p 2200
```

### Issue 3: Keys exist but Docker can't find them

**Check Docker can see the keys:**
```bash
docker exec smai-app ls -la /home/appuser/.ssh/
```

**If empty:** Check docker-compose volume mount
```bash
# Should see:
- ${USERPROFILE:-${HOME}}/.ssh:/home/appuser/.ssh:ro
```

### Issue 4: Works locally but not in production

**Verify environment variable:**

On deployment server:
```bash
echo $HOME
# Should output: /home/your-user

echo $USERPROFILE
# Should be empty on Linux
```

The docker-compose uses `${USERPROFILE:-${HOME}}` which means:
- Use `$USERPROFILE` if set (Windows)
- Otherwise use `$HOME` (Linux)

---

## Quick Reference

| Environment | Variable | Expands To |
|-------------|----------|------------|
| **Windows** | `${USERPROFILE}` | `C:\Users\Bipin Joshi` |
| **Linux** | `${HOME}` | `/home/username` |
| **Docker Compose** | `${USERPROFILE:-${HOME}}` | Uses Windows or Linux automatically |

---

## Security Notes

✅ **Private keys never leave the deployment server**  
✅ **Keys are mounted read-only in Docker**  
✅ **Each server has its own keys**  
✅ **Keys are not committed to Git**  

---

## Summary

1. ✅ **Local works** because your Windows machine has SSH keys
2. ❌ **Deployment fails** because deployment server has no SSH keys
3. 🔧 **Fix:** Generate SSH keys on deployment server
4. 📤 **Deploy:** Copy public key to target server (31.97.224.45)
5. 🔄 **Restart:** Restart Docker container
6. ✅ **Done:** Application works on deployment server

---

## One-Line Setup Command

Run this on your deployment server:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N "" && \
ssh-copy-id -i ~/.ssh/id_ed25519.pub meetri@31.97.224.45 -p 2200 && \
ssh -i ~/.ssh/id_ed25519 meetri@31.97.224.45 -p 2200 "echo 'SSH setup complete!'" && \
docker-compose restart
```

This will:
1. Generate SSH key
2. Copy it to target server (you'll enter password once)
3. Test connection
4. Restart Docker

Done! 🎉
