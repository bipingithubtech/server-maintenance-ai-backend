# 🔑 SSH Key Auto-Discovery

## How It Works

The application now **automatically discovers and uses SSH keys** from any user's system without hardcoding paths!

### Architecture

```
User's System (~/.ssh/)           Docker Container (/root/.ssh/)
├── id_ed25519         ─────────→ ├── id_ed25519
├── id_ed25519.pub               ├── id_ed25519.pub
├── id_rsa                       ├── id_rsa
├── id_rsa.pub                   ├── id_rsa.pub
├── known_hosts                  ├── known_hosts
└── config                       └── config
```

The entire `~/.ssh` directory is mounted read-only into the container, making all SSH keys available.

---

## Frontend Usage

Users don't need to provide SSH keys anymore! Just send:

```json
{
  "host": "31.97.224.45",
  "port": 2200,
  "username": "meetri"
}
```

The backend will:
1. ✅ Auto-discover SSH keys from `~/.ssh/`
2. ✅ Try keys in order: `id_ed25519`, `id_rsa`, `id_ecdsa`, `id_dsa`
3. ✅ Fall back to password if provided
4. ✅ Return clear error if no valid authentication found

---

## Authentication Priority

The system tries authentication methods in this order:

1. **Specific key** (if `key_filename` provided)
   ```json
   {"host": "...", "username": "...", "key_filename": "/root/.ssh/custom_key"}
   ```

2. **Auto-discovered keys** (tries all found keys)
   ```json
   {"host": "...", "username": "..."}
   ```

3. **Password authentication** (if `password` provided)
   ```json
   {"host": "...", "username": "...", "password": "..."}
   ```

---

## How Auto-Discovery Works

### Backend Logic

```python
def _discover_ssh_keys():
    # Look in ~/.ssh/ for common key files
    common_keys = ["id_ed25519", "id_rsa", "id_ecdsa", "id_dsa"]
    
    found_keys = []
    for key_name in common_keys:
        key_path = f"~/.ssh/{key_name}"
        if file_exists(key_path):
            found_keys.append(key_path)
    
    return found_keys  # Pass to paramiko to try each one
```

### Paramiko Behavior

When given multiple keys, paramiko:
- Tries each key in order
- Stops at first successful authentication
- Falls back to password if all keys fail

---

## User Setup Requirements

### For Any User Running Docker

**Step 1:** Ensure SSH keys are in `~/.ssh/`

```bash
# Check if you have SSH keys
ls -la ~/.ssh/

# If not, generate one
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
```

**Step 2:** Copy public key to target server

```bash
# Copy your public key to the server you want to access
ssh-copy-id -i ~/.ssh/id_ed25519.pub user@server -p port

# Test it works
ssh -i ~/.ssh/id_ed25519 user@server -p port
```

**Step 3:** Start Docker

```bash
docker-compose up -d
```

That's it! The container automatically mounts your `~/.ssh/` directory.

---

## Benefits

✅ **Works for everyone** - No hardcoded paths  
✅ **Zero configuration** - Just ensure SSH keys exist in `~/.ssh/`  
✅ **Multi-user friendly** - Each user's keys are automatically used  
✅ **Secure** - Keys are mounted read-only  
✅ **Flexible** - Falls back to password if no keys work  

---

## Docker Compose Configuration

```yaml
volumes:
  # Mount user's .ssh directory (auto-discovers keys)
  - ~/.ssh:/root/.ssh:ro
```

The `~` automatically expands to:
- **Linux/Mac:** `/home/username/.ssh` or `/Users/username/.ssh`
- **Windows:** `C:/Users/username/.ssh`

---

## Troubleshooting

### Issue: "Authentication failed: Server only accepts SSH key authentication"

**Cause:** You don't have any SSH keys, or they're not authorized on the target server.

**Solution:**
1. Generate SSH key: `ssh-keygen -t ed25519`
2. Copy to server: `ssh-copy-id user@server -p port`
3. Restart Docker: `docker-compose restart`

### Issue: "No SSH keys found in ~/.ssh"

**Cause:** Your `~/.ssh` directory is empty or doesn't exist.

**Solution:**
```bash
mkdir -p ~/.ssh
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
```

### Issue: Still prompting for password

**Cause:** Your SSH key isn't authorized on the target server.

**Solution:**
```bash
# Copy your public key to server
cat ~/.ssh/id_ed25519.pub | ssh user@server -p port "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

---

## Security Considerations

1. ✅ **Read-only mount** - Container can't modify your SSH keys
2. ✅ **No key copying** - Keys stay on your host machine
3. ✅ **No hardcoding** - No sensitive data in code or config
4. ✅ **Per-user isolation** - Each user's keys are separate

---

## Advanced: Custom Keys

If you want to use a specific key (not auto-discovered):

```json
{
  "host": "31.97.224.45",
  "port": 2200,
  "username": "meetri",
  "key_filename": "/root/.ssh/custom_key_name"
}
```

Just ensure the key exists in your `~/.ssh/` directory!
