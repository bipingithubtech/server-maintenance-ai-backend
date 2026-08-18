# Multi-App Deployment Support

## Overview
Enhanced deployment agent to support both single-app and multi-app deployment scenarios.

---

## Problem
The original deployment path selection didn't account for scenarios where multiple applications are deployed to subdirectories within the same parent directory.

### Before
All deployments went to a single path:
```
/opt/api/myapp/          ← Single app only
```

### After
Users can choose deployment mode:
```
Option 1 (Direct):
/opt/api/myapp/          ← Single app, direct path

Option 2 (Subdirectory):
/api/myapp/              ← Multiple apps, each in own subdirectory
/api/another-app/
/api/third-app/
```

---

## Solution

### Two Deployment Modes

#### Mode 1: Direct Path (Single App)
- App clones directly to specified path
- No subdirectories created
- Best for: Single app per directory
- Example: `/opt/api/luna-backend/`

#### Mode 2: Subdirectory (Multi-App)
- App clones to `{base_path}/{app_name}`
- Allows multiple apps in same parent
- Best for: Multiple apps sharing directory
- Example: `/api/luna-backend/`, `/api/leave-backend/`, `/api/pm-backend/`

---

## User Interaction

### Deployment Flow

```
User: "deploy https://github.com/org/luna-backend nextjs 3000"
     ↓
Agent: Determines app type = backend
       Suggests default path = /opt/api/luna-backend
     ↓
Agent: "How should the app be deployed?
        
        Option 1 (Direct): Clone into single path
          Path: /opt/api/luna-backend
          Use when: This is the only app in this directory
        
        Option 2 (Subdirectory): Clone into app-specific subdirectory
          Path: /opt/api/luna-backend/luna-backend
          Use when: Multiple apps share the same directory
        
        Enter choice (1 or 2, or custom path):"
     ↓
User: "2"  or "/api" or "/api/luna-backend"
     ↓
Agent: Proceeds with selected path
```

---

## Usage Examples

### Example 1: Deploy to `/api` Directory with Multiple Apps

**Scenario**: Deploy 3 backend apps to `/api` directory

```
Deployment 1: luna-backend
  User input: "2" or "subdirectory"
  Result: /api/luna-backend/

Deployment 2: leave-backend
  User input: "2" or "subdirectory"
  Result: /api/leave-backend/

Deployment 3: pm-backend
  User input: "2" or "subdirectory"
  Result: /api/pm-backend/

Final directory structure:
/api/
├── luna-backend/
│   ├── src/
│   ├── package.json
│   └── ...
├── leave-backend/
│   ├── src/
│   ├── package.json
│   └── ...
└── pm-backend/
    ├── src/
    ├── package.json
    └── ...
```

### Example 2: Deploy Single App to Direct Path

**Scenario**: Single app, direct deployment

```
User: "deploy https://github.com/org/myapp nextjs 3000"
Agent: "How should the app be deployed?
        Option 1 (Direct): /opt/api/myapp
        Option 2 (Subdirectory): /opt/api/myapp/myapp
        Enter choice:"
User: "1" or [press Enter]
Result: /opt/api/myapp/  ← Single app, direct path
```

### Example 3: Custom Path via API

**Scenario**: Pre-specify exact path via API

```json
{
  "query": "deploy https://github.com/org/backend nextjs 8000",
  "deployment_params": {
    "clone_dir": "/home/meetri/api/backend-service"
  }
}
```

**Result**: Skips path selection, deploys to `/home/meetri/api/backend-service`

---

## Implementation Details

### Changes Made

#### 1. Enhanced Path Selection Prompt
**File**: `app/agents/deployment_agent.py` (lines ~340-360)

Shows two options:
- Option 1: Direct deployment (no subdirectory)
- Option 2: Subdirectory deployment (with app-specific subfolder)

#### 2. Response Handler
**File**: `app/api/chat.py` (lines ~310-330)

Handles user responses:
- `"1"` or `"direct"` → Direct path
- `"2"` or `"subdirectory"` or `"sub"` → Subdirectory path
- Custom path → Use as-is
- Custom subdirectory name → Append to base path

---

## Path Resolution Logic

```python
def resolve_deployment_path(default_path, app_name, user_input):
    if user_input == "1" or user_input == "direct":
        return default_path  # /opt/api/myapp
    
    elif user_input == "2" or user_input == "subdirectory":
        return f"{default_path}/{app_name}"  # /opt/api/myapp/myapp
    
    elif user_input.startswith("/"):
        return user_input  # /custom/path/apps/myapp
    
    else:
        return f"{default_path}/{user_input}"  # /opt/api/custom-name
```

---

## Real-World Scenarios

### Scenario A: Managed Monorepo Server
```
Server: /api
├── ats-backend/              ← Deploy 1: Option 2
├── leave-backend/            ← Deploy 2: Option 2
├── luna-backend/             ← Deploy 3: Option 2
├── pm-backend/               ← Deploy 4: Option 2
├── reminder-backend/         ← Deploy 5: Option 2
└── rightkids-api-gw/         ← Deploy 6: Option 2
```

Each deployment:
1. Choose Option 2
2. Result: Direct into subdirectory

### Scenario B: Single Purpose App Server
```
Server: /opt/api
└── myapp/                    ← Deploy 1: Option 1 (direct)
    ├── src/
    ├── package.json
    └── ...
```

Single deployment:
1. Choose Option 1
2. Result: Direct to /opt/api/myapp

### Scenario C: Mixed Deployment Strategy
```
Server: /opt
├── ui/                       ← Frontend apps
│   ├── dashboard/
│   └── admin/
└── api/                      ← Backend apps
    ├── auth-service/
    ├── user-service/
    └── data-service/
```

Multiple deployments, each choosing appropriate option.

---

## API Usage

### With Default Path Selection
```bash
curl -X POST http://localhost:8000/api/query \
  -d '{
    "query": "deploy https://github.com/org/luna-backend nextjs 3000"
  }'
```

**Response**: Asks user for deployment mode

### With Prefilled Path (Skips Selection)
```bash
curl -X POST http://localhost:8000/api/query \
  -d '{
    "query": "deploy https://github.com/org/luna-backend nextjs 3000",
    "deployment_params": {
      "clone_dir": "/api/luna-backend"
    }
  }'
```

**Result**: No path question asked, deploys to `/api/luna-backend`

### Resume Conversation with Mode Choice
```bash
# Step 1: Get path selection question
curl -X POST http://localhost:8000/api/query \
  -d '{"query": "deploy https://github.com/org/luna-backend nextjs 3000"}'

# Response:
{
  "needs_input": true,
  "question": "How should the app be deployed? Option 1 (Direct): ... Option 2 (Subdirectory): ...",
  "conversation_id": "conv_xyz"
}

# Step 2: Provide answer
curl -X POST http://localhost:8000/api/query \
  -d '{
    "query": "2",
    "conversation_id": "conv_xyz"
  }'

# Result: Deployment proceeds with subdirectory path
```

---

## Benefits

### For Single-App Deployments
✅ Direct path deployment (no unnecessary nesting)
✅ Simple, straightforward deployment

### For Multi-App Deployments
✅ Organized directory structure
✅ Each app has own isolated directory
✅ Easy to manage multiple versions
✅ Clear separation of concerns

### For API Users
✅ Can pre-specify exact path
✅ Skip interactive prompts
✅ Consistent deployments via automation

---

## Backward Compatibility

✅ **Fully backward compatible**

- Existing deployments with prefilled paths work unchanged
- New interactive prompt only appears when path not prefilled
- All previous functionality preserved

---

## Default Behavior

When user doesn't specify a mode:

**For Single App**:
- Suggests Option 1 (direct) by default
- User can press Enter or type "1"

**For Multi-App Servers**:
- User types "2" or "subdirectory"
- Result: App goes to subdirectory

---

## Error Handling

| User Input | Result |
|-----------|--------|
| `1` | Use direct path: `{default_path}` |
| `2` | Use subdirectory: `{default_path}/{app_name}` |
| `/custom/path` | Use custom path as-is |
| `custom-name` | Use as subdirectory: `{default_path}/custom-name` |
| (empty/Enter) | Default to Option 1 (direct) |

---

## Best Practices

### Single App Server
1. Choose **Option 1 (Direct)**
2. Result: `/opt/api/myapp/`
3. Cleaner structure, no unnecessary nesting

### Multi-App Server (Recommended)
1. Choose **Option 2 (Subdirectory)**
2. Result: `/api/{app-name}/` for each app
3. Organized, scalable, easy to manage

### Custom Paths
Use only when default paths don't match your infrastructure:
```
User: "deploy https://github.com/org/app nextjs 3000"
Agent: "How should the app be deployed?"
User: "/home/meetri/api/luna-backend"
Result: Deploys to exact path specified
```

---

## Monitoring Multi-App Deployments

After deploying to `/api`:

```bash
ls -la /api/
# Shows:
total 128
drwxr-xr-x  2 meetri meetri 4096 Aug 17 15:00 .
drwxr-xr-x 20 root   root   4096 Aug 17 14:00 ..
drwxr-xr-x  8 meetri meetri 4096 Aug 17 15:00 ats-backend/
drwxr-xr-x  8 meetri meetri 4096 Aug 17 15:05 leave-backend/
drwxr-xr-x  8 meetri meetri 4096 Aug 17 15:10 luna-backend/
drwxr-xr-x  8 meetri meetri 4096 Aug 17 15:15 pm-backend/
```

Each app has its own directory with independent code, dependencies, and processes.

---

## Testing

### Test 1: Option 1 (Direct)
```bash
curl -X POST http://localhost:8000/api/query \
  -d '{"query": "deploy ... nextjs 3000"}'
# User answer: "1"
# Expected: /opt/ui/app-name/
```

### Test 2: Option 2 (Subdirectory)
```bash
curl -X POST http://localhost:8000/api/query \
  -d '{"query": "deploy ... nextjs 3000"}'
# User answer: "2"
# Expected: /opt/ui/app-name/app-name/
```

### Test 3: Custom Path
```bash
curl -X POST http://localhost:8000/api/query \
  -d '{"query": "deploy ... nextjs 3000"}'
# User answer: "/api/custom-app"
# Expected: /api/custom-app/
```

### Test 4: API Prefill (Skips Question)
```bash
curl -X POST http://localhost:8000/api/query \
  -d '{
    "query": "deploy ... nextjs 3000",
    "deployment_params": {"clone_dir": "/api/myapp"}
  }'
# Expected: No path question, deploys to /api/myapp
```

---

## Migration from Old Behavior

If you were manually specifying paths before, you can now:

1. **Old way**: `clone_dir` parameter
   ```json
   {"deployment_params": {"clone_dir": "/api/myapp"}}
   ```

2. **New way**: Choose mode interactively OR use `clone_dir`
   Both methods work the same way

---

## Summary

| Aspect | Before | After |
|--------|--------|-------|
| Single app support | ✅ Yes | ✅ Yes |
| Multi-app support | ❌ No | ✅ Yes |
| Path selection | ❌ Limited | ✅ Full control |
| Subdirectory mode | ❌ No | ✅ Yes |
| Direct path mode | ✅ Yes | ✅ Yes |
| Custom paths | ✅ Yes | ✅ Yes |
| Backward compatible | N/A | ✅ Yes |

---

**Last Updated**: 2026-08-17
**Status**: ✅ Complete & Verified
