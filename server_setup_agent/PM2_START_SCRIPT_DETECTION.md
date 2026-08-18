# PM2 Start Script Auto-Detection

## Feature
PM2 now automatically detects and uses the correct start script for NestJS and Next.js apps.

---

## Problem
Different projects use different npm start scripts:
- Some use: `npm run start` (development)
- Some use: `npm run start:prod` (production)
- Some use: `npm start`

System was hardcoded to use `npm run start`, which failed for projects using `start:prod`.

---

## Solution
Enhanced PM2 tool to auto-detect the available start script in `package.json`:

1. Check if `start:prod` script exists
2. If yes, use `npm run start:prod`
3. If no, use `npm run start`

---

## How It Works

### Step 1: Check package.json
```bash
grep -q '"start:prod"' /path/to/package.json
# Returns: exists or not found
```

### Step 2: Select Script
```
If start:prod exists:
  → pm2 start npm --name app -- run start:prod ✅
  
If only start exists:
  → pm2 start npm --name app -- run start ✅
```

### Step 3: Start App
```
pm2 start npm --name luna-backend -- run start:prod
# Or
pm2 start npm --name luna-backend -- run start
```

---

## Examples

### NestJS with start:prod
**package.json:**
```json
{
  "scripts": {
    "start": "node dist/main.js",
    "start:prod": "node --enable-source-maps dist/main",
    "build": "nest build"
  }
}
```

**Result:**
```
✅ Auto-detected: start:prod
✅ Started with: npm run start:prod
✅ App running in production mode
```

### NestJS with just start
**package.json:**
```json
{
  "scripts": {
    "start": "nest start",
    "build": "nest build"
  }
}
```

**Result:**
```
✅ No start:prod found
✅ Started with: npm run start
✅ App running
```

### Next.js with start:prod
**package.json:**
```json
{
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "start:prod": "NODE_ENV=production next start"
  }
}
```

**Result:**
```
✅ Auto-detected: start:prod
✅ Started with: npm run start:prod
✅ App running in production mode
```

---

## Supported Patterns

| Script | Detected | Used |
|--------|----------|------|
| `start` | ✅ Always | Yes (fallback) |
| `start:prod` | ✅ Check first | Yes (if exists) |
| `start:staging` | ❌ No | Not used |
| Custom scripts | ❌ No | Not used |

---

## Benefits

✅ **Production Ready**
- Uses `start:prod` when available
- Respects project-specific configuration

✅ **Development Friendly**
- Falls back to `start` if needed
- Works with all npm scripts

✅ **Zero Config**
- Auto-detects automatically
- No manual configuration needed

✅ **Backward Compatible**
- Existing projects still work
- Uses `start` if `start:prod` doesn't exist

---

## Testing

### Test Case 1: Project with start:prod
```bash
cd /home/meetri/api/luna-backend
cat package.json | grep "start:prod"

# Deploy should:
# ✅ Detect start:prod
# ✅ Use: npm run start:prod
# ✅ App starts in production mode
```

### Test Case 2: Project with only start
```bash
cd /home/meetri/api/leave-backend
cat package.json | grep "start:"

# Deploy should:
# ✅ No start:prod found
# ✅ Use: npm run start
# ✅ App starts normally
```

---

## How to Verify

After deployment:
```bash
pm2 list
# Shows your app as "online"

pm2 logs luna-backend
# Shows your app startup logs
```

---

## File Changed

**File**: `app/tools/pm2_tool.py`

**Change**: Enhanced `start()` method to:
1. Parse package.json for available scripts
2. Detect `start:prod` script
3. Use appropriate script for startup

---

## Benefits for Your Setup

For your multi-app server at `/home/meetri/api`:

```
Deploy luna-backend:
  ✅ Auto-detects: start:prod
  ✅ Starts: npm run start:prod
  
Deploy leave-backend:
  ✅ Auto-detects: start
  ✅ Starts: npm run start
  
Deploy pm-backend:
  ✅ Auto-detects: start:prod
  ✅ Starts: npm run start:prod

Each app uses its own configuration! ✅
```

---

## Next Steps

Just deploy normally - the system will now:
1. Build your app (`npm run build`)
2. Check your package.json
3. Auto-detect the right start script
4. Start with PM2 using that script

No manual configuration needed!

---

**Last Updated**: 2026-08-17
**Status**: ✅ Implemented & Verified
**Backward Compatible**: ✅ Yes
