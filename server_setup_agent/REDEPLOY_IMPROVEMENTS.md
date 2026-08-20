# Redeploy Tool Improvements

## Issues Fixed

### Issue 1: Parent Directory Handling ✅
**Problem**: User says "dir is /home/meetri/ui" but app is actually in `/home/meetri/ui/pm-frontend`

**Before**:
```
❌ Git pull failed: /home/meetri/ui is not a git repository
```

**After**:
```
✓ Detected that /home/meetri/ui is not a git repo
✓ Found app in subdirectory: /home/meetri/ui/pm-frontend
✓ Using /home/meetri/ui/pm-frontend for redeploy
```

**Implementation**:
1. Check if provided path is a git repository
2. If not, check if `{provided_path}/{app_name}` exists and is a git repo
3. If found, use the subdirectory path automatically
4. If not found, provide clear error message with what was checked

---

### Issue 2: Branch Support Added ✅
**Problem**: No way to specify which branch to deploy

**Solution**: Added optional `branch` parameter to `redeploy_app` tool

**Usage Examples**:
```
User: "redeploy pm-frontend from dev branch"
→ Switches to dev branch, then pulls and deploys

User: "redeploy luna-backend from main branch"
→ Switches to main branch, then pulls and deploys

User: "redeploy my-app"
→ Uses current branch (no branch switch)
```

**Implementation**:
1. Check current branch
2. If different from requested branch, run `git checkout {branch}`
3. Show branch switch confirmation
4. Continue with normal redeploy workflow

---

## Enhanced Features

### 1. Smarter Path Detection
Now checks 6 locations instead of 4:
```python
possible_paths = [
    f"/home/meetri/api/{app_name}",
    f"/home/meetri/ui/{app_name}",   # NEW
    f"/opt/api/{app_name}",
    f"/opt/ui/{app_name}",            # NEW
    f"/opt/{app_name}",
    f"/var/www/{app_name}",
]
```

### 2. Better Error Messages
**Before**:
```
❌ Could not find app directory
```

**After**:
```
❌ '/home/meetri/ui' is not a git repository.

I checked:
  • /home/meetri/ui - not a git repo
  • /home/meetri/ui/pm-frontend - not found

Please provide the exact path to the git repository for 'pm-frontend'.
```

### 3. Branch Information in Logs
```
[OPS] REDEPLOY: pm-frontend (branch: dev)
[REDEPLOY] Switching to branch: dev
```

---

## Complete Workflow

### Scenario 1: User provides parent directory
```
User: "redeploy pm-frontend dir is /home/meetri/ui"

System:
1. Checks /home/meetri/ui/.git → not found
2. Checks /home/meetri/ui/pm-frontend/.git → found!
3. Uses /home/meetri/ui/pm-frontend
4. Git pull
5. npm install
6. npm run build
7. pm2 restart pm-frontend

Result: ✅ App redeployed from correct subdirectory
```

### Scenario 2: User specifies branch
```
User: "redeploy pm-frontend from develop branch"

System:
1. Finds app at /home/meetri/ui/pm-frontend
2. Checks current branch → "main"
3. Switches to "develop" branch
4. Git pull from develop
5. Build and restart

Result: ✅ App redeployed from correct branch
```

### Scenario 3: App name only
```
User: "redeploy pm-frontend"

System:
1. Tries /home/meetri/api/pm-frontend → not found
2. Tries /home/meetri/ui/pm-frontend → found!
3. Uses current branch (no switch)
4. Git pull
5. Build and restart

Result: ✅ Auto-detected correct path
```

---

## API Usage

### Basic redeploy:
```json
{
  "query": "redeploy pm-frontend",
  "credentials": {...}
}
```

### Redeploy with custom path:
```json
{
  "query": "redeploy pm-frontend dir is /home/meetri/ui",
  "credentials": {...}
}
```

### Redeploy with branch:
```json
{
  "query": "redeploy pm-frontend from develop branch",
  "credentials": {...}
}
```

### Redeploy with path and branch:
```json
{
  "query": "redeploy pm-frontend from staging branch, dir is /home/meetri/ui",
  "credentials": {...}
}
```

---

## Natural Language Understanding

The LLM understands various phrasings:

**Branch specification**:
- "from dev branch"
- "from develop"
- "use staging branch"
- "deploy main branch"
- "switch to production"

**Path specification**:
- "dir is /path/to/app"
- "path is /path/to/app"
- "located at /path/to/app"
- "in /path/to/app"

---

## Technical Details

### Tool Definition:
```python
{
    "name": "redeploy_app",
    "description": "Redeploy app (git pull, build, restart)",
    "parameters": {
        "app_name": {"type": "string"},      # Required
        "app_path": {"type": "string"},      # Optional
        "branch": {"type": "string"},        # Optional (NEW)
    },
}
```

### Git Operations:
```bash
# Check current branch
git branch --show-current

# Switch branch (if needed)
git checkout {branch}

# Pull latest
git pull
```

### Subdirectory Detection:
```bash
# Check if path is git repo
test -d /path/.git && echo 'is_git' || echo 'not_git'

# Check if subdirectory is git repo
test -d /path/app-name/.git && echo 'found' || echo 'not'
```

---

## Benefits

1. ✅ **User-friendly**: Handles parent directories automatically
2. ✅ **Flexible**: Supports branch specification
3. ✅ **Informative**: Clear error messages guide users
4. ✅ **Smart**: Auto-detects correct paths
5. ✅ **Safe**: Validates git repos before attempting operations
6. ✅ **Transparent**: Shows what it's checking and why

---

## Examples in Action

### Example 1: Parent Directory + Branch
```
Input: "redeploy pm-frontend from dev, dir is /home/meetri/ui"

Output:
✓ Detected subdirectory: /home/meetri/ui/pm-frontend
🔀 Switched to branch 'dev'
📦 Git pull:
  Already up to date.
📦 Installing dependencies...
  npm install completed
🔨 Building application...
  Build completed
🔄 Restarting PM2 process...
✅ PM2 app 'pm-frontend' redeployed successfully
```

### Example 2: Auto-detect + Current Branch
```
Input: "redeploy luna-backend"

Output:
✓ Found app at: /home/meetri/api/luna-backend
✓ Already on branch 'main'
📦 Git pull:
  Updating abc123..def456
  Fast-forward
   src/index.ts | 5 +++++
   1 file changed, 5 insertions(+)
📦 Installing dependencies...
  npm install completed
🔄 Restarting PM2 process...
✅ PM2 app 'luna-backend' redeployed successfully
```

---

## Testing Checklist

- [x] Parent directory detection works
- [x] Subdirectory auto-discovery works
- [x] Branch parameter accepted
- [x] Branch switching works
- [x] Current branch preserved if not specified
- [x] Error messages are clear
- [x] Path auto-detection includes ui directories
- [x] No syntax errors
- [x] No diagnostics issues

---

## Future Enhancements (Optional)

1. **List available branches** before asking user to choose
2. **Show uncommitted changes** warning before pulling
3. **Stash changes** automatically if needed
4. **Tag deployments** for rollback capability
5. **Deployment history** tracking
6. **Rollback command** to previous version
7. **Multi-app redeploy** (redeploy all apps in a directory)

---

## Conclusion

The redeploy tool now handles:
- ✅ Parent directories (auto-discovers subdirectories)
- ✅ Branch selection (optional parameter)
- ✅ Better error messages (shows what was checked)
- ✅ Smarter path detection (6 locations instead of 4)
- ✅ Clear logging (shows branch info)

No breaking changes - all existing usage patterns still work!
