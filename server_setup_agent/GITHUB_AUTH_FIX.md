# Deployment Agent - GitHub Authentication Fix

## Problem
Deployments were failing during git clone with authentication errors:

```
remote: Invalid username or token. Password authentication is not supported for Git operations.
fatal: Authentication failed for 'https://github.com/Meetri-IT/luna-frontend.git/'
```

### Root Causes
1. **Invalid Token**: Token was expired, revoked, or had insufficient permissions
2. **No Validation**: Token was not validated before attempting clone
3. **Headless TTY Issues**: Git was configured with `credential.helper=''` causing TTY prompts on headless servers
4. **Poor Error Messages**: When auth failed, users didn't know why or how to fix it

---

## Solution

### 1. Added Token Validation

**New Method**: `_validate_github_token(token, repo_url)`

```python
def _validate_github_token(self, token: str, repo_url: str) -> bool:
    """
    Validate GitHub token by attempting a minimal API call.
    Returns True if token is valid, False otherwise.
    """
    import urllib.request as _req, json as _json
    try:
        # Try to fetch user info with the token
        req = _req.Request(
            "https://api.github.com/user",
            headers={
                "Authorization": f"token {token}",
                "User-Agent": "server-setup-agent",
                "Accept": "application/vnd.github.v3+json",
            },
        )
        with _req.urlopen(req, timeout=6) as resp:
            data = _json.loads(resp.read().decode())
            if "login" in data:
                logger.info(f"[DEPLOY] GitHub token validated for user: {data['login']}")
                return True
    except Exception as e:
        logger.warning(f"[DEPLOY] GitHub token validation failed: {str(e)[:100]}")
    return False
```

**When It's Called**:
- After token is collected (from prefill, env var, or user input)
- BEFORE attempting git clone
- If validation fails, user is asked to provide a valid token

### 2. Improved Token Handling in `_complete_context`

**Before**:
```python
if not github_token:
    # Ask for token if private repo
else:
    self._github_token = github_token
```

**After**:
```python
if not github_token:
    # Ask for token if private repo
else:
    # Validate the token before using it
    is_valid = self._validate_github_token(github_token, ctx.github_url)
    if not is_valid:
        # Ask user to provide valid token with helpful message
        raise NeedsInputError(
            f"The GitHub token appears to be invalid or expired.\n"
            f"Error: Authentication failed.\n\n"
            f"Please provide a valid GitHub Personal Access Token (PAT) with repo read access:\n"
            f"1. Create one at: https://github.com/settings/tokens/new?scopes=repo\n"
            f"2. Make sure the token has not expired\n"
            f"3. If using an org token, ensure it has access to this repo",
            ...
        )
    self._github_token = github_token
```

### 3. Fixed Git Clone Command

**Before** (TTY issue on headless servers):
```bash
sudo git clone --config credential.helper='' \
  --branch develop --single-branch \
  https://token@github.com/org/repo.git /path
```

**After** (No TTY prompt needed):
```bash
sudo -E bash -c 'GIT_ASKPASS=echo git clone \
  --branch develop --single-branch \
  https://token@github.com/org/repo.git /path'
```

**Why This Works**:
- `GIT_ASKPASS=echo` tells git to use `echo` instead of waiting for TTY input
- `-E` preserves environment variables for sudo
- Works in headless/non-interactive environments

### 4. Enhanced Error Messages in `_step_clone`

Added try-catch with specific error handling:

```python
try:
    self._run(
        f"sudo -E bash -c 'GIT_ASKPASS=echo git clone "
        f"--branch {ctx.branch} --single-branch "
        f"{clone_url} {ctx.app_path}'"
    )
except RuntimeError as e:
    error_msg = str(e)
    if "Authentication failed" in error_msg or "Invalid username or token" in error_msg:
        raise RuntimeError(
            f"Git clone failed due to authentication error.\n"
            f"Details: {error_msg}\n\n"
            f"Possible causes:\n"
            f"1. GitHub token is invalid or expired\n"
            f"2. Token does not have access to this repository\n"
            f"3. Repository is private and token is missing\n\n"
            f"Solution: Provide a valid GitHub PAT at https://github.com/settings/tokens/new?scopes=repo"
        )
    raise
```

---

## Changes Made

### File: `app/agents/deployment_agent.py`

1. **Added `_validate_github_token()` method** (lines ~167-190)
   - Validates token using GitHub API
   - Logs username if valid
   - Returns True/False

2. **Updated `_complete_context()` method** (lines ~410-450)
   - Calls `_validate_github_token()` before proceeding
   - Provides helpful error message if token is invalid
   - Asks user to provide valid token

3. **Fixed `_step_clone()` method** (lines ~540-580)
   - Changed from `--config credential.helper=''` to `GIT_ASKPASS=echo`
   - Added try-catch for authentication errors
   - Provides specific error messages

---

## New Deployment Flow

### With Valid Token
```
User: "deploy https://github.com/org/repo nextjs 3000 token ghp_xxx..."
     ↓
Agent: Validates token with GitHub API
     ↓
Agent: ✓ Token is valid (user: john.doe)
     ↓
Agent: Proceeds with deployment
```

### With Invalid Token
```
User: "deploy https://github.com/org/repo nextjs 3000 token ghp_expired..."
     ↓
Agent: Validates token with GitHub API
     ↓
Agent: ✗ Token validation failed
     ↓
Agent: Asks user to provide valid token with helpful instructions
     ↓
User: Provides new valid token
     ↓
Agent: Validates new token
     ↓
Agent: ✓ Proceeds with deployment
```

### Clone Failure Handling
```
User: Deployment in progress...
     ↓
Agent: Attempts git clone with token
     ↓
Agent: ✗ Clone fails: "Invalid username or token"
     ↓
Agent: Shows detailed error message with causes and solutions
     ↓
Agent: User fixes token and tries again
```

---

## User Experience Improvements

### 1. Validation Before Deployment
- Token is validated early (before clone attempt)
- No wasted time on clone that will fail
- Faster feedback loop

### 2. Clear Error Messages
Instead of cryptic git error, users now see:

```
The GitHub token appears to be invalid or expired.
Error: Authentication failed.

Please provide a valid GitHub Personal Access Token (PAT) with repo read access:
1. Create one at: https://github.com/settings/tokens/new?scopes=repo
2. Make sure the token has not expired
3. If using an org token, ensure it has access to this repo
```

### 3. Headless Server Support
- No TTY prompts that hang on remote servers
- Uses `GIT_ASKPASS=echo` for non-interactive environments
- Works with SSH deployments

---

## Testing the Fix

### Test 1: Valid Token
```bash
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "deploy https://github.com/org/private-repo nextjs 3000",
    "deployment_params": {
      "github_token": "ghp_valid_token_here"
    }
  }'
```
**Expected**: Token validated, deployment proceeds

### Test 2: Invalid Token
```bash
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "deploy https://github.com/org/private-repo nextjs 3000",
    "deployment_params": {
      "github_token": "ghp_invalid_token"
    }
  }'
```
**Expected**: `needs_input=true` with message asking for valid token

### Test 3: Public Repo (No Token)
```bash
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "deploy https://github.com/owner/public-repo nextjs 3000"
  }'
```
**Expected**: Deployment proceeds without token

---

## How to Create a Valid GitHub Token

1. Go to: https://github.com/settings/tokens/new?scopes=repo
2. Give it a name: "Server Deployment AI"
3. Select scope: `repo` (Full control of private repositories)
4. Expiration: Set to a reasonable period (90 days, 1 year, or never)
5. Click "Generate token"
6. Copy the token immediately (you won't see it again)
7. Use in deployment:
   ```bash
   curl -X POST http://localhost:8000/api/query \
     -d '{
       "deployment_params": {
         "github_token": "ghp_your_token_here"
       }
     }'
   ```

---

## Troubleshooting

### Error: "Invalid username or token"

**Causes**:
1. Token expired - create new token
2. Token was revoked - create new token
3. Token doesn't have repo access - check scopes
4. Token is for different GitHub account - use correct token
5. Token for GitHub Enterprise - token must be from same instance

**Solution**:
1. Create new token: https://github.com/settings/tokens/new?scopes=repo
2. Ensure it's from same GitHub instance as repo
3. Ensure `repo` scope is selected
4. Try deployment again

### Error: "No such device or address"
**Cause**: TTY-related issue (old version)
**Solution**: Update to latest version with `GIT_ASKPASS=echo` fix

### Error: "Repository not found"
**Causes**:
1. Repository is private and token lacks access
2. Repository is under organization and token needs org access
3. Wrong GitHub instance (GitHub vs GitHub Enterprise)

**Solution**:
1. Verify repo exists: https://github.com/owner/repo
2. Verify you have access
3. Create new token with proper permissions

---

## Technical Details

### Why Validation?
- **Early detection**: Fails fast before spending time on clone
- **Better UX**: User gets helpful message immediately
- **Cost saving**: Avoids wasted API calls and bandwidth

### Why GIT_ASKPASS=echo?
- **Non-interactive**: Works on headless servers (no TTY)
- **Secure**: Token stays in process, not in shell history
- **Standard**: Used by many deployment tools

### Token Format
GitHub tokens follow this pattern:
- **Old**: `ghp_` prefix (Personal Access Token - classic)
- **New**: `github_pat_` prefix (Fine-grained PAT)
- Minimum 40 characters
- Cannot contain spaces or special shell chars

---

## Backward Compatibility
✅ Fully backward compatible
- Existing deployments work unchanged
- Validation is silent for valid tokens
- Invalid tokens now caught earlier

---

## Next Steps (Optional Enhancements)

- [ ] Add rate limiting info to validation errors
- [ ] Cache token validation results (1 hour TTL)
- [ ] Support GitHub Enterprise tokens
- [ ] Add organization token support
- [ ] Log token validation attempts for security audit

---

## Summary

| Aspect | Before | After |
|--------|--------|-------|
| Token Validation | None | ✅ Validates before clone |
| Error Messages | Generic git errors | ✅ Specific, actionable |
| TTY Issues | ❌ Fails on headless | ✅ Works everywhere |
| Time to Failure | Long (after clone) | ✅ Fast (immediate) |
| User Experience | Frustrating | ✅ Clear guidance |

---

**Last Updated**: 2026-08-17
**Status**: Ready for Production ✅
