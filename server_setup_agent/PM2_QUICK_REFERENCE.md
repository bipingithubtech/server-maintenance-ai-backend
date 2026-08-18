# PM2 Quick Reference — What to Say to AI

## Scenario 1: Start an App with PM2 (After Failed Deployment)

### What You Say:
```
"start luna-backend with pm2"
```

### What AI Does:
1. Checks if `start:prod` script exists in package.json
2. Uses `start:prod` if found, otherwise uses `start`
3. Runs: `pm2 start npm --name luna-backend -- run start:prod`
4. Confirms success

### Variations:
```
"start luna-backend with pm2"        # Uses auto-detected script
"start pm-backend with pm2"          # Another app
"start /home/meetri/api/luna-backend with pm2"  # Full path
"start luna-backend with pm2 port 3000"  # With port
```

---

## Scenario 2: Check All Running Apps

### What You Say:
```
"show me all pm2 apps"
```

### What AI Does:
1. Runs: `pm2 list --no-color`
2. Shows table with:
   - Process name
   - Status (online, stopped, erroring)
   - CPU usage
   - Memory usage
   - Restart count

### Variations:
```
"show pm2 status"
"check pm2"
"what apps are running"
```

---

## Scenario 3: View App Logs

### What You Say:
```
"show logs for luna-backend"
```

### What AI Does:
1. Runs: `pm2 logs luna-backend --lines 50 --nostream`
2. Shows last 50 lines of app output

### Variations:
```
"show logs for luna-backend"              # Default 50 lines
"show logs for luna-backend, 100 lines"   # Custom number
"show me luna-backend error logs"         # AI will adjust
"last 200 lines of luna-backend logs"     # Another phrasing
```

---

## Scenario 4: Restart a Stopped App

### What You Say:
```
"restart luna-backend with pm2"
```

### What AI Does:
Same as "start luna-backend with pm2" — PM2 will restart if already running, start if stopped.

---

## Scenario 5: Multiple Apps

### Deploy multiple apps, then start each:
```
User: "start luna-backend with pm2"
AI: Starts luna-backend
→
User: "start pm-backend with pm2"
AI: Starts pm-backend
→
User: "show me all pm2 apps"
AI: Shows both running
```

---

## How It Works Behind the Scenes

### Step 1: App Path Resolution
```
Input: "luna-backend"
↓
Check if path starts with "/" ?
  No → add /home/meetri/api/ prefix
↓
Resolved: /home/meetri/api/luna-backend
```

### Step 2: Script Detection
```
Resolved path: /home/meetri/api/luna-backend
↓
Check package.json for "start:prod" ?
  Yes → use "start:prod"
  No → use "start"
↓
Script: start:prod (auto-detected)
```

### Step 3: PM2 Start
```
app_name = luna-backend
script = start:prod
↓
Run: pm2 start npm --name luna-backend -- run start:prod
↓
✅ App started
```

---

## Common Questions

### Q1: Do I need to provide the full path?
**A**: No, short names like `luna-backend` work automatically. They're resolved to `/home/meetri/api/{app_name}`.

Full path example:
```
"start /home/meetri/api/luna-backend with pm2"  # Also works
```

### Q2: Which script does it use?
**A**: Auto-detects from package.json:
- If `start:prod` exists → uses it (production)
- Otherwise → uses `start` (fallback)

See in package.json:
```json
{
  "scripts": {
    "start": "nest start",
    "start:prod": "node dist/main.js"
  }
}
```

### Q3: How do I set the port?
**A**: Include port in your command:
```
"start luna-backend with pm2 port 3000"
"start luna-backend with pm2 on port 8003"
```

### Q4: What if PM2 is not installed?
**A**: AI will see error and can install it:
```
"install pm2 globally"
AI: Runs npm install -g pm2
```

### Q5: How do I stop an app?
**A**: Use run_command directly:
```
"stop luna-backend"
AI: Runs pm2 stop luna-backend
```

---

## Real Example: Multi-App Deployment

**Day 1 - Deploy all apps**:
```
User: Deploy luna-backend to /home/meetri/api
AI: [clone, install, build, start with pm2]
↓
User: Deploy pm-backend to /home/meetri/api
AI: [clone, install, build, start with pm2]
↓
User: Show me all pm2 apps
AI: Shows both running
```

**Day 2 - App crashed, need to restart**:
```
User: start luna-backend with pm2
AI: [auto-detects script, restarts]
↓
User: show logs for luna-backend
AI: Shows last 50 lines, you see the error
↓
User: fix the issue then restart again
```

---

## Advanced Usage (If Needed)

### Use different script:
```
"start luna-backend with npm run start"  # Force 'start' script
"start luna-backend with npm run dev"    # Force 'dev' script
```

### Check full status:
```
"run pm2 status"      # Raw pm2 status
"run pm2 show luna-backend"  # Detailed single app
```

### View specific line count:
```
"show last 200 lines of luna-backend logs"
"tail -100 of luna-backend logs"
```

---

## Checklist for Deployment + PM2

- [ ] Clone repo
- [ ] npm install
- [ ] npm run build
- [ ] Start with PM2 ← "start luna-backend with pm2"
- [ ] Verify running ← "show me all pm2 apps"
- [ ] Check logs if issue ← "show logs for luna-backend"

---

**When to use**: After deployment, anytime you need to manage PM2 apps  
**Who uses it**: You — just tell AI what you need in natural language
