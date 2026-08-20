# Quick Reference - New PM2 & Redeploy Features

## ✅ All Tasks Completed!

### New PM2 Management Commands

#### 1. Stop PM2 Application
```
User: "stop leave-frontend"
User: "stop the luna-backend app"
```
**What it does**: Stops the PM2 process without removing it from the process list

---

#### 2. Restart PM2 Application  
```
User: "restart leave-frontend"
User: "restart luna-backend"
```
**What it does**: Restarts the PM2 process (keeps it in the list, just restarts)

---

#### 3. Delete PM2 Application
```
User: "delete old-app from PM2"
User: "remove test-app"
```
**What it does**: Completely removes the app from PM2 process list

---

### New Redeploy Feature 🚀

#### Automatic Redeploy (PM2 Apps)
```
User: "redeploy leave-frontend"
User: "update luna-backend"
User: "deploy latest code for my-app"
```

**What it does**:
1. ✅ Git pull latest code
2. ✅ npm install (installs dependencies)
3. ✅ npm run build (if build script exists)
4. ✅ pm2 restart {app-name}

**Auto-detects paths**:
- `/home/meetri/api/{app-name}`
- `/opt/api/{app-name}`
- `/opt/{app-name}`
- `/var/www/{app-name}`

---

#### Automatic Redeploy (Docker Apps)
```
User: "redeploy server-maintenance-ai-backend"
User: "update docker app my-docker-app"
```

**What it does**:
1. ✅ Git pull latest code
2. ✅ docker compose down
3. ✅ docker compose build
4. ✅ docker compose up -d

**Detects**: Checks if app is running in Docker automatically

---

#### Redeploy with Custom Path
```
User: "redeploy my-app in /custom/path/my-app"
User: "update luna-backend at /home/user/projects/luna-backend"
```

**What it does**: Uses the specified path instead of auto-detection

---

## Example Conversations

### Scenario 1: Stop and Start App
```
User: "stop leave-frontend"
AI: ✅ App 'leave-frontend' stopped

User: "start leave-frontend"  
AI: [Uses existing pm2_start - will restart if already exists]
```

---

### Scenario 2: Redeploy After Code Changes
```
User: "I just pushed new code to leave-frontend, redeploy it"
AI: 
📦 Git pull:
Already up to date. / [new commits]

📦 Installing dependencies...
[npm install output]

🔨 Building application...
Build completed

🔄 Restarting PM2 process...
✅ PM2 app 'leave-frontend' redeployed successfully
```

---

### Scenario 3: Docker App Update
```
User: "update the backend docker app"
AI:
📦 Git pull:
[git output]

🛑 Stopping containers...
[docker compose down output]

🔨 Building new image...
[docker compose build output]

🚀 Starting containers...
[docker compose up -d output]

✅ Docker app redeployed successfully
```

---

## How It Works Behind the Scenes

### PM2 Tools
- Directly execute PM2 commands via SSH
- Log all operations
- Return user-friendly status messages

### Redeploy Tool
1. **Path Detection**: Tries 4 common paths automatically
2. **App Type Detection**: Checks if Docker or PM2
3. **Stack Detection**: Checks for package.json or requirements.txt
4. **Workflow Execution**: Runs appropriate commands for app type
5. **Status Reporting**: Returns detailed progress updates

---

## Supported Application Types

### Node.js/NPM Apps (PM2)
- ✅ Next.js
- ✅ NestJS
- ✅ Express
- ✅ React (build and serve)
- ✅ Any npm-based app

### Python Apps (PM2)
- ✅ FastAPI
- ✅ Flask  
- ✅ Django
- ✅ Any requirements.txt-based app

### Docker Apps
- ✅ Any app with docker-compose.yml
- ✅ Multi-container setups
- ✅ Custom Dockerfiles

---

## Error Handling

### Path Not Found
```
❌ Could not find app directory for 'my-app'. Please specify app_path.
```
**Solution**: Specify the full path in your request

### No docker-compose.yml
```
⚠️ No docker-compose.yml found in /path/to/app
```
**Solution**: App might not be a Docker app, or file is missing

### App Not in PM2
```
⚠️ App 'my-app' not found in PM2. You may need to start it manually.
```
**Solution**: App needs to be started first with pm2_start

---

## Integration with Existing Features

### Works with OPS Agent
- All tools available through natural language
- LLM decides which tool to call based on user intent
- Part of the existing ops_agent.py workflow

### Works with Supervisor
- Supervisor routes PM2/redeploy queries to ops agent
- Seamless integration with other agents

### Works with General Agent
- User can ask "how to stop PM2 app" → General agent answers
- Then user can say "stop my-app" → OPS agent executes

---

## Testing Checklist

- [x] PM2 stop tool defined
- [x] PM2 restart tool defined  
- [x] PM2 delete tool defined
- [x] Redeploy tool defined
- [x] Dispatch handlers implemented
- [x] Path auto-detection works
- [x] Docker detection works
- [x] PM2 detection works
- [x] Node.js app support
- [x] Python app support
- [x] Error messages clear

---

## Ready to Use! 🎉

All features are now implemented and ready for production use. The system will:
- ✅ Understand natural language requests
- ✅ Auto-detect application types and paths
- ✅ Execute appropriate workflows
- ✅ Provide clear status updates
- ✅ Handle errors gracefully

No configuration needed - just ask the AI naturally!
