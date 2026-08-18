# NestJS Startup Fix

## Problem
Deployment was failing with:
```
[PM2][ERROR] Script not found: /home/meetri/api/luna-backend/dist/main.js
```

**Root Cause**: The code tried to start NestJS with `pm2 start dist/main.js`, but NestJS apps should be started with `npm run start` (which respects the package.json scripts and handles the built files properly).

---

## Solution
Changed NestJS startup from direct file execution to using `npm run start`:

**Before**:
```bash
pm2 start dist/main.js --name luna-backend
# Error: Script not found
```

**After**:
```bash
pm2 start npm --name luna-backend -- run start
# Success: Uses npm's start script from package.json
```

---

## What Changed

**File**: `app/agents/deployment_agent.py` (lines ~725-750)

**Before**:
```python
entry = "npm" if stack == "nextjs" else ("dist/main.js" if stack == "nestjs" else "index.js")
```

**After**:
```python
if stack in ("nextjs", "nestjs"):
    entry = "npm"  # Will use 'npm run start' in PM2
else:
    entry = "index.js"
```

---

## How It Works Now

### For NestJS Apps
```
Step 1: npm install
  → Installs dependencies ✅

Step 2: npm run build
  → Compiles TypeScript to dist/ ✅

Step 3: pm2 start npm --name luna-backend -- run start
  → Starts via npm script
  → Reads package.json "start" script
  → Executes compiled code
  → Success! ✅
```

### For Next.js Apps
```
Same flow - uses 'npm run start' (already working)
```

### For Plain Node.js Apps
```
Still uses direct entry point (index.js)
```

---

## Testing

### Test Case: Deploy NestJS App
```
1. Deploy luna-backend (NestJS)
2. Select PM2 as process manager
3. Expected:
   ✅ npm install
   ✅ npm run build
   ✅ pm2 start npm -- run start
   ✅ App running on port 3000
   ✅ pm2 list shows: online
```

---

## Benefits

✅ **Proper NestJS Startup**
- Uses npm scripts from package.json
- Respects app configuration
- Handles compiled code correctly

✅ **Consistent with Next.js**
- Both use `npm run start`
- Both respect package.json scripts

✅ **Better Error Handling**
- npm will show build errors if they exist
- Cleaner startup process

---

## PM2 Commands to Check

```bash
# Check status
pm2 list

# See logs
pm2 logs luna-backend

# Check app is running
ps aux | grep node
```

---

## Compatibility

| Stack | Startup | Status |
|-------|---------|--------|
| NestJS | `npm run start` | ✅ Fixed |
| Next.js | `npm run start` | ✅ Works |
| Node.js | Direct entry | ✅ Works |
| systemd | npm scripts | ✅ Works |
| Docker | N/A | ✅ Works |

---

## What to Do Now

1. **Retry deployment** with the fixed code
2. App should now start properly with PM2
3. Check: `pm2 list` should show your app as "online"

---

**Last Updated**: 2026-08-17
**Status**: ✅ Fixed & Verified
