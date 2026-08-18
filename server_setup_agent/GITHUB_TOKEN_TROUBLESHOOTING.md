# GitHub Token Troubleshooting Guide

## Quick Diagnosis

### Step 1: Check Token Format
```bash
echo $GITHUB_TOKEN
```

Should start with `ghp_` or `github_pat_` and be at least 40 characters.

```
✅ Valid:     ghp_abcdefghijklmnopqrstuvwxyz1234567890
✅ Valid:     github_pat_11AAAA_abcdefghijklmnopqrstuvwxyz
❌ Invalid:   my_token_123
❌ Invalid:   (empty or missing)
```

### Step 2: Test Token with API
```bash
curl -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/user
```

**Expected Response**:
```json
{
  "login": "your_username",
  "id": 12345,
  "name": "Your Name",
  ...
}
```

**Error Response (Invalid Token)**:
```json
{
  "message": "Bad credentials",
  "documentation_url": "..."
}
```

### Step 3: Check Token Permissions
```bash
curl -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/user/repos?per_page=1
```

**Expected**: List of repositories
**Error**: `"message": "API rate limit exceeded"`  or `"message": "Bad credentials"`

---

## Common Errors & Solutions

### Error 1: "Invalid username or token"

```
fatal: Authentication failed for 'https://github.com/owner/repo.git/'
```

**Diagnosis Steps**:

1. **Check if token exists**:
   ```bash
   [ -z "$GITHUB_TOKEN" ] && echo "Token is empty!" || echo "Token exists"
   ```

2. **Check token format**:
   ```bash
   echo "$GITHUB_TOKEN" | wc -c
   # Should be 40+ characters
   ```

3. **Test token validity**:
   ```bash
   curl -s -H "Authorization: token $GITHUB_TOKEN" \
     https://api.github.com/user | grep "login"
   ```
   - Should show your GitHub username
   - If not, token is invalid or expired

**Solutions** (in order):

1. **Create a new token**:
   - Go to: https://github.com/settings/tokens/new?scopes=repo
   - Name: "Server Deployment"
   - Scope: Select `repo` checkbox
   - Expiration: 90 days or 1 year
   - Click "Generate"
   - Copy immediately (won't show again)

2. **Update environment**:
   ```bash
   export GITHUB_TOKEN="ghp_your_new_token"
   ```

3. **Retry deployment**:
   ```bash
   curl -X POST http://localhost:8000/api/query \
     -d '{"query": "deploy https://github.com/owner/repo nextjs 3000"}'
   ```

---

### Error 2: "Repository not found"

```
fatal: repository 'https://github.com/owner/private-repo.git/' not found
```

**Cause**: Repository is private and token doesn't have access

**Diagnosis**:
```bash
# Test if repo is accessible with token
curl -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/owner/private-repo

# If you get 404, token lacks access
```

**Solutions**:

1. **If repo is in your account**:
   - Token should have `repo` scope
   - Verify at: https://github.com/settings/tokens

2. **If repo is in an organization**:
   - Token needs organization access
   - Go to: https://github.com/settings/tokens
   - Click token name
   - Under "Organization access": Grant access to org

3. **If repo is under GitHub Enterprise**:
   - Use GitHub Enterprise token, not github.com token
   - Token must be from same GitHub instance

---

### Error 3: "No such device or address"

```
fatal: could not read Password for 'https://ghp_xxx@github.com': 
No such device or address
```

**Cause**: Git trying to prompt for password on headless server (no TTY)

**Solution** (should be fixed with `GIT_ASKPASS=echo`):
- Restart the deployment service:
  ```bash
  systemctl restart server_setup_agent
  # or
  pm2 restart server_setup_agent
  ```
- Ensure you're using latest version with the fix
- Redeploy

---

### Error 4: "Token Validation Failed"

```
The GitHub token appears to be invalid or expired.
Error: Authentication failed.
```

**Cause**: Token validation happened, token is definitely bad

**Immediate Action**:
1. Create new token: https://github.com/settings/tokens/new?scopes=repo
2. Provide it to the deployment agent
3. Retry

---

### Error 5: "API rate limit exceeded"

```
message: "API rate limit exceeded. (But here's the good news: 
Authenticated requests get a higher rate limit)"
```

**Cause**: Too many GitHub API calls without token, or token has exhausted limit

**Solutions**:

1. **Wait for rate limit reset**:
   - Unauthenticated: 1 hour
   - Authenticated: 12 hours

2. **Check rate limit**:
   ```bash
   curl -H "Authorization: token $GITHUB_TOKEN" \
     https://api.github.com/rate_limit
   ```

3. **Use a different token**:
   - Create new token for this account
   - Or use different GitHub account

---

## Token Management

### Create New Token
1. Go to: https://github.com/settings/tokens/new
2. Name: "Server Deployment AI"
3. Expiration: 90 days (or as needed)
4. Scopes: ✅ `repo` (Full control)
5. Generate → Copy immediately

### Store Token Securely

**Option 1: Environment Variable**
```bash
export GITHUB_TOKEN="ghp_xxxxxxxxxxxx"
```

**Option 2: .env File** (server)
```bash
echo "GITHUB_TOKEN=ghp_xxxxxxxxxxxx" >> ~/.bashrc
source ~/.bashrc
```

**Option 3: ~/.github_token File**
```bash
echo "ghp_xxxxxxxxxxxx" > ~/.github_token
chmod 600 ~/.github_token
```

**Option 4: Deployment Params** (API)
```json
{
  "deployment_params": {
    "github_token": "ghp_xxxxxxxxxxxx"
  }
}
```

### Rotate Token
1. Go to: https://github.com/settings/tokens
2. Find old token
3. Click "Delete"
4. Create new token (follow "Create New Token" above)
5. Update all places using the token

### Check Token Expiration
```bash
curl -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/user | grep "created_at\|updated_at"
```

---

## Advanced Troubleshooting

### Check git configuration on server
```bash
git config --list | grep credential
```

### Test git clone directly
```bash
git clone https://ghp_token@github.com/owner/repo.git /tmp/test-clone
```

### Check git version
```bash
git --version
```

### Enable git debugging
```bash
GIT_CURL_VERBOSE=1 GIT_TRACE=1 git clone https://...
```

### Check SSH key alternative
If using SSH:
```bash
ssh -T git@github.com
# Should return: Hi username! You've successfully authenticated
```

---

## Prevention

### 1. Set Token Expiration Reminder
- Set expiration to 90 days
- Note expiration date in calendar
- 2 weeks before: Create new token
- 1 week after expiration: Verify old one is deleted

### 2. Use Token with Minimal Scope
- Only select `repo` scope needed
- Avoid `admin` or `delete_repo` scopes

### 3. Rotate Regularly
- Quarterly (every 3 months)
- After team member leaves
- After security incident

### 4. Store Securely
- Never commit to git
- Don't share in logs/messages
- Use .gitignore for .env files

---

## Support

If you still have issues:

1. **Verify repo is public and accessible**:
   ```bash
   curl -L https://github.com/owner/repo
   ```

2. **Test with curl**:
   ```bash
   curl -H "Authorization: token $GITHUB_TOKEN" \
     https://api.github.com/repos/owner/repo
   ```

3. **Check deployment logs**:
   ```bash
   pm2 logs server_setup_agent | tail -100
   ```

4. **Ask for help with**:
   - GitHub URL
   - Token status (✅ valid / ❌ invalid)
   - Error message from deployment
   - Repository type (public/private/org)

---

## Quick Reference

| Problem | Command to Test | Expected |
|---------|-----------------|----------|
| Token valid? | `curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/user` | Shows your username |
| Token expired? | `curl ... /user` | No "Bad credentials" error |
| Repo accessible? | `curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/repos/owner/repo` | Shows repo info |
| Rate limit? | `curl ... /rate_limit` | `limit: 5000` for authenticated |

---

**Last Updated**: 2026-08-17
**Version**: 1.0
