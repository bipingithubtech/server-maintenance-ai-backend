# Sudo Password via UI (Better Than Passwordless Sudo)

Instead of configuring passwordless sudo on your server, you can now **provide your sudo password through the UI** when connecting to your server. The AI will use it automatically for all sudo commands.

## How It Works

### 1. Connect to Server (with sudo password)

When you connect to your server from the frontend, provide:

```json
{
  "host": "31.97.224.45",
  "username": "meetri",
  "password": "your_ssh_password",
  "key_filename": null,
  "sudo_password": "your_sudo_password"
}
```

Or if using SSH key auth:

```json
{
  "host": "31.97.224.45",
  "username": "meetri",
  "key_filename": "/path/to/private/key",
  "sudo_password": "your_sudo_password"
}
```

### 2. AI Uses Password Automatically

When deployment runs commands like:
```bash
sudo apt-get install nodejs
```

The system converts it to:
```bash
echo 'your_sudo_password' | sudo -S apt-get install nodejs
```

This provides the password via stdin (no interactive prompt needed).

### 3. Deployment Continues

Installation proceeds without any password prompts:
- ✅ nodejs installed
- ✅ npm packages installed
- ✅ Application deployed
- ✅ Done!

## Frontend Implementation

Your frontend should ask for both passwords:

```
┌─────────────────────────────────┐
│  Connect to Server              │
├─────────────────────────────────┤
│ Host:          31.97.224.45     │
│ Username:      meetri           │
│ SSH Password:  [hidden field]   │
│ Sudo Password: [hidden field]   │
│                                 │
│ [Connect] [Cancel]              │
└─────────────────────────────────┘
```

## How It's Implemented

### 1. Frontend Sends Credentials

```javascript
const response = await fetch('/api/v1/query', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    query: 'deploy my-app',
    credentials: {
      executor_type: 'ssh',
      host: '31.97.224.45',
      username: 'meetri',
      password: 'ssh_password',
      sudo_password: 'sudo_password'  // ← NEW
    }
  })
});
```

### 2. Backend Stores Password

Credentials flow through:
- `ServerCredentials` (pydantic model) → includes `sudo_password`
- `to_config()` → includes `sudo_password` in executor config
- `SSHExecutor.__init__()` → stores `self.sudo_password`

### 3. Sudo Commands Automatically Use Password

In `SSHExecutor._exec()`:

```python
if command.startswith("sudo") and self.sudo_password:
    # Convert: sudo apt-get install nodejs
    # To:      echo 'password' | sudo -S apt-get install nodejs
    command = f"echo '{self.sudo_password}' | sudo -S {command[4:].lstrip()}"
```

## Security Considerations

### ✅ Secure
- Password sent over **HTTPS only** (frontend to backend)
- **SSH encrypted** from backend to server (backend to server)
- Password **never logged** or displayed
- Password **only in memory** during command execution
- **Your SSH key** controls who can connect

### ⚠️ Be Aware
- Password is temporarily stored in backend memory
- Use **HTTPS** for frontend connections
- Use **SSH key auth** when possible (more secure than password auth)
- Keep your SSH key private

### 🔐 Better Practice
1. Use SSH key auth instead of password auth
2. Use sudo password instead of passwordless sudo
3. Consider rotating passwords periodically
4. Use HTTPS for all frontend connections

## Comparison: Three Options

| Option | Setup | Security | Convenience |
|--------|-------|----------|-------------|
| **Passwordless sudo** | One-time visudo edit | Lower (any sudo command works) | Highest (no prompt) |
| **Sudo password in UI** | None (this feature) | Higher (password provided each session) | High (automatic per session) |
| **Manual entry each time** | None | Same as UI option | Low (requires manual input) |

## Which Should I Choose?

### Use UI Sudo Password If:
- ✅ Security is important
- ✅ You want flexibility (password per session)
- ✅ Fewer deployments (occasional use)
- ✅ You value per-connection control

### Use Passwordless Sudo If:
- ✅ Maximum convenience needed
- ✅ Trusted internal network only
- ✅ Frequent deployments (many per day)
- ✅ You control the server completely

## Using Both

You can use **both** methods:

1. Set up **passwordless sudo** on your server (for occasional manual SSH use)
2. Provide **sudo_password in the UI** (for AI deployments)

The UI password takes precedence, so deployments will work even if passwordless sudo isn't configured.

## Example: Complete Deployment Flow

### 1. Connect with Sudo Password
```json
{
  "query": "connect",
  "credentials": {
    "host": "31.97.224.45",
    "username": "meetri",
    "key_filename": "~/.ssh/id_rsa",
    "sudo_password": "my_server_sudo_password"
  }
}
```

Response:
```json
{
  "connected": true,
  "user": "meetri",
  "host": "31.97.224.45"
}
```

### 2. Deploy Application
```json
{
  "query": "deploy https://github.com/org/app.git stack nodejs port 3000",
  "credentials": {
    "host": "31.97.224.45",
    "username": "meetri",
    "key_filename": "~/.ssh/id_rsa",
    "sudo_password": "my_server_sudo_password"
  }
}
```

The AI automatically:
1. Clones the repo
2. Runs `sudo apt-get install nodejs` (password provided via stdin)
3. Runs `npm install`
4. Starts the app
5. Configures nginx

All without prompting for password!

## Technical Details

### Command Transformation

Before:
```bash
sudo apt-get install nodejs
sudo systemctl start nginx
sudo docker build -t app .
```

After (when sudo_password provided):
```bash
echo 'password' | sudo -S apt-get install nodejs
echo 'password' | sudo -S systemctl start nginx
echo 'password' | sudo -S docker build -t app .
```

### What `sudo -S` Does

- `-S` flag reads password from standard input (stdin)
- Password doesn't appear in command history
- Works in headless SSH environments
- More secure than passing password in command line

## Troubleshooting

### "sudo: password is incorrect"

Check that you provided the correct sudo password in the UI.

### "sudo: sorry, you must have a tty to run sudo"

This shouldn't happen with the `-S` flag. If it does, ensure:
- You provided `sudo_password` in credentials
- Backend is passing it correctly to executor

### "Password appears in logs"

**Report this as a security issue!** Passwords should never be logged. The system is designed to redact them.

## Files Modified

| File | Change |
|------|--------|
| `app/api/schemas.py` | Added `sudo_password` to `ServerCredentials` |
| `app/api/connect.py` | Added `sudo_password` handling to connection test |
| `app/executors/ssh_executor.py` | Added password injection for sudo commands |
| `app/tools/package_tool.py` | Updated error messages with both options |

## Summary

**Better than passwordless sudo:**
- ✅ No server config needed
- ✅ Password per-session only
- ✅ Better security control
- ✅ Can use SSH keys + sudo password together

**Just provide:** `sudo_password` in the connection credentials, and the AI handles the rest!

---

## Next Steps

1. ✅ Frontend asks for sudo_password when connecting
2. ✅ Pass it in the credentials to `/api/v1/query`
3. ✅ AI automatically uses it for all sudo commands
4. ✅ Deployment works without passwordless sudo!
