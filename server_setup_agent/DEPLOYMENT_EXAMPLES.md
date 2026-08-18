# Deployment Path Selection - Example Scenarios

## Scenario 1: Interactive CLI Deployment with Default Path

```
User: python -m app.main
> deploy https://github.com/acme/backend-api.git fastapi 8000

[DEPLOY] LLM Processing...
[DEPLOY] App type: backend
[DEPLOY] Auto-selected clone_dir: /opt/api/backend-api

? Where should the app be deployed?
  Default: /opt/api/backend-api
  (Press Enter to use default, or enter a custom path like /home/user/apps/myapp):
  
User: [Press Enter]

[DEPLOY] Using deployment path: /opt/api/backend-api

? Which process manager should be used to run the app?
  Options: 1. pm2  2. systemd  3. docker
  (default: pm2):
  
User: 1

? Which branch to deploy?
  Available branches: 1. main 2. develop 3. staging
  Enter branch name or number (default: main):
  
User: [Press Enter]

✓ Deployment started...
```

---

## Scenario 2: Interactive CLI with Custom Path

```
User: deploy https://github.com/acme/frontend-app.git nextjs 3000

[DEPLOY] App type: frontend
[DEPLOY] Auto-selected clone_dir: /opt/ui/frontend-app

? Where should the app be deployed?
  Default: /opt/ui/frontend-app
  (Press Enter to use default, or enter a custom path like /home/user/apps/myapp):
  
User: /srv/web/myapp

[DEPLOY] Using deployment path: /srv/web/myapp

? Which process manager should be used to run the app?
  Options: 1. pm2  2. systemd  3. docker
  (default: pm2):
  
User: 3

✓ Deployment started with Docker...
```

---

## Scenario 3: API Call with Prefilled Path

### Request
```bash
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "deploy https://github.com/acme/app.git react 3000",
    "deployment_params": {
      "branch": "main",
      "process_manager": "pm2",
      "clone_dir": "/home/deploy/apps/frontend"
    }
  }'
```

### Response
```json
{
  "agent": "deployment",
  "reason": "User wants to deploy a React application",
  "result": "Deployment plan...",
  "needs_input": false
}
```

**Note**: Because `clone_dir` was provided in `deployment_params`, the agent **skips the path question** and uses the provided path directly.

---

## Scenario 4: API Call - Multi-Step with Path Question

### Step 1: Initial Request (No Path Provided)
```bash
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "deploy https://github.com/acme/app.git nodejs 8080"
  }'
```

### Step 1 Response
```json
{
  "agent": "deployment",
  "needs_input": true,
  "question": "Where should the app be deployed?\nDefault: /opt/api/app\n(Press Enter to use default, or enter a custom path like /home/user/apps/myapp):",
  "conversation_id": "conv_abc123xyz"
}
```

### Step 2: User Provides Custom Path
```bash
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "/opt/nodejs/custom-location",
    "conversation_id": "conv_abc123xyz"
  }'
```

### Step 2 Response
```json
{
  "agent": "deployment",
  "needs_input": true,
  "question": "Which process manager should be used to run the app?\nOptions: 1. pm2  2. systemd  3. docker\n(default: pm2):",
  "conversation_id": "conv_abc123xyz"
}
```

### Step 3: User Selects Process Manager
```bash
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "systemd",
    "conversation_id": "conv_abc123xyz"
  }'
```

### Step 3 Response
```json
{
  "agent": "deployment",
  "needs_input": true,
  "question": "Which branch to deploy?\nAvailable branches: 1. main 2. develop\nEnter branch name or number (default: main):",
  "conversation_id": "conv_abc123xyz"
}
```

### Step 4: User Confirms Branch
```bash
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "main",
    "conversation_id": "conv_abc123xyz"
  }'
```

### Step 4 Response
```json
{
  "agent": "deployment",
  "needs_input": false,
  "result": "Deployment started...",
  "reason": "Deployment plan confirmed and execution started"
}
```

---

## Scenario 5: Path Validation Edge Cases

### Invalid Path - Too Long
```
User: /this/is/a/very/long/path/that/exceeds/system/limits/...
[DEPLOY] Warning: Path may be too long for filesystem
[DEPLOY] Proceeding anyway: /this/is/a/very/long/...
```

### Path with Spaces
```
User: /opt/my apps/frontend
[DEPLOY] Using path: /opt/my apps/frontend
✓ Successfully created deployment directory
```

### Relative Path (Converted to Absolute)
```
User: ./apps/myapp
[DEPLOY] Converting to absolute path: /current/working/dir/apps/myapp
```

---

## Quick Reference: Default Paths

| App Type | Default Path Pattern |
|----------|-------------------|
| Frontend (react, vite, angular, nextjs) | `/opt/ui/{app_name}` |
| Backend (fastapi, flask, django, nodejs, nestjs) | `/opt/api/{app_name}` |
| Other | `/opt/{app_name}` |

**Example:**
- Repo: `https://github.com/acme/my-awesome-app.git`
- App name: `my-awesome-app`
- Frontend default: `/opt/ui/my-awesome-app`
- Backend default: `/opt/api/my-awesome-app`
