# Sudo Password Issue - Root Cause & Solution

## Root Cause
The sudo password is not being passed from the frontend to the deployment agent, so SSH commands fail with:
```
sudo: a terminal is required to read the password
```

## Why This Happens
- SSH connections don't allocate pseudo-terminals
- Sudo commands need password piped via `echo 'password' | sudo -S command`
- The `sudo_password` field exists in backend but is not being populated from UI

## Solution

### Backend (Already Done ✅)
1. ✅ `ServerCredentials` has `sudo_password` field
2. ✅ `SSHExecutor` injects password: `sudo cmd` → `echo 'pass' | sudo -S cmd`
3. ✅ `.env` supports `SUDO_PASSWORD` for default servers

### Frontend (Needs Implementation ❌)
The UI needs to collect sudo password when connecting to a server.

**Add to server connection form:**
```json
{
  "host": "31.97.224.45",
  "username": "meetri",
  "password": "ssh_password_here",  // or use key_filename
  "sudo_password": "Meetri@12345",  // NEW - password for sudo commands
  "executor_type": "ssh"
}
```

## Testing Without UI Changes

For now, you can test by manually passing sudo_password in the API request:

```bash
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "deploy https://github.com/bipingithubtech/server-maintenance-ai.git port 5173",
    "credentials": {
      "executor_type": "ssh",
      "host": "31.97.224.45",
      "username": "meetri",
      "key_filename": "/path/to/key",
      "sudo_password": "Meetri@12345"
    }
  }'
```

## Alternative: Configure Passwordless Sudo on Server

On the remote server, configure passwordless sudo:
```bash
sudo visudo
# Add this line:
meetri ALL=(ALL) NOPASSWD: ALL
```

Then test:
```bash
sudo echo "test"  # Should work without password prompt
```

**Note:** You mentioned you already configured this on the server (`meetri ALL=(ALL) NOPASSWD: ALL`), but SSH commands still ask for password. This suggests the sudoers file might not be properly configured or there's a syntax issue.

## Current Status

- ✅ Backend supports sudo password injection
- ❌ Frontend doesn't collect/send sudo password
- ❌ Passwordless sudo not working on server (despite configuration)

## Next Steps

**Option 1:** Update frontend to collect sudo_password in server credentials form
**Option 2:** Debug why passwordless sudo isn't working on the server
**Option 3:** Use the ops agent after deployment to configure Nginx/SSL manually
