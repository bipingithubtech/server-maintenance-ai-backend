# Deployment Agent - Path Selection Enhancement

## Overview
Added a new feature to the deployment agent that **asks users where they want to deploy the application** before proceeding with the deployment. Previously, the deployment path was automatically determined based on app type (`/opt/ui/` for frontend, `/opt/api/` for backend) without user input.

## Changes

### 1. File: `app/agents/deployment_agent.py`

**Added deployment path prompt in `_complete_context` method (line ~340-353)**

Now when deploying, the agent will:
1. Determine the app type (frontend vs backend)
2. Calculate a default deployment path (e.g., `/opt/ui/myapp`)
3. **Ask the user where to deploy** with the default path shown
4. Allow the user to either:
   - Press Enter to accept the default path
   - Provide a custom deployment path

```python
# ── Deployment path ───────────────────────────────────────────
if pf.get("clone_dir"):
    ctx.app_path = pf["clone_dir"].rstrip("/")
else:
    ctx._resolve_app_path()
    default_path = ctx.app_path
    logger.info(f"[DEPLOY] Auto-selected clone_dir: {default_path}")
    
    # Ask user if they want to use the default path or provide a custom one
    raise NeedsInputError(
        f"Where should the app be deployed?\n"
        f"Default: {default_path}\n"
        f"(Press Enter to use default, or enter a custom path like /home/user/apps/myapp):",
        {"step": "need_deploy_path", "ctx_partial": {
            "github_url": ctx.github_url, "stack": ctx.stack,
            "port": ctx.port, "domain": ctx.domain,
            "app_type": ctx.app_type,
        }, "default_path": default_path, "prefill": pf, "agent": "deployment"}
    )
```

### 2. File: `app/api/chat.py`

**Added handler for "need_deploy_path" step in `_resume_conversation` method (line ~310-320)**

When the user provides an answer to the deployment path question, the API now handles it properly:

```python
elif step == "need_deploy_path":
    answer = user_answer.strip()
    default_path = agent_state.get("default_path", "")
    if not answer or answer.lower() in ("default", "skip", ""):
        # User accepted default path
        prefill["clone_dir"] = default_path
    else:
        # User provided custom path
        prefill["clone_dir"] = answer
    # Merge ctx_partial so all core fields survive the resume
    prefill.update({k: v for k, v in agent_state.get("ctx_partial", {}).items() if k not in prefill or not prefill[k]})
    new_query = orig_request.get("query", "")
```

## Deployment Workflow

### Before (Old Flow)
```
User: "deploy https://github.com/org/repo nextjs 3000"
     ↓
Agent: Process app type → Auto-select /opt/ui/repo
     ↓
Agent: Ask for process manager
     ↓
Agent: Ask for branch
     ↓
Agent: Ask for GitHub token (if private)
```

### After (New Flow)
```
User: "deploy https://github.com/org/repo nextjs 3000"
     ↓
Agent: Process app type → Auto-select /opt/ui/repo
     ↓
Agent: ✨ Ask "Where should the app be deployed?"
       Suggest default: /opt/ui/repo
       User can: [Press Enter] or [Enter custom path]
     ↓
Agent: Ask for process manager
     ↓
Agent: Ask for branch
     ↓
Agent: Ask for GitHub token (if private)
```

## User Experience Examples

### Example 1: Accept Default Path
```
AI: Where should the app be deployed?
    Default: /opt/ui/myapp
    (Press Enter to use default, or enter a custom path like /home/user/apps/myapp):
User: [Press Enter]
Result: App deployed to /opt/ui/myapp
```

### Example 2: Custom Path
```
AI: Where should the app be deployed?
    Default: /opt/ui/myapp
    (Press Enter to use default, or enter a custom path like /home/user/apps/myapp):
User: /srv/apps/myapp
Result: App deployed to /srv/apps/myapp
```

### Example 3: Via Prefill (API)
If using the API with `deployment_params`:
```json
{
  "github_url": "https://github.com/org/repo.git",
  "stack": "nextjs",
  "port": "3000",
  "clone_dir": "/custom/path"
}
```
The deployment path prompt is skipped and uses the provided path directly.

## Benefits

1. **User Control**: Users can now specify exactly where they want their app deployed
2. **Flexibility**: Not limited to default `/opt/ui/` and `/opt/api/` paths
3. **Backward Compatible**: Default paths are suggested for quick deployment
4. **API-Friendly**: Can be pre-filled via API parameters to skip the prompt
5. **Better UX**: Clear feedback about the default location with option to customize

## Order of Deployment Questions

The deployment agent now asks for these things in this order:

1. **Core Info (LLM)**: GitHub URL, Stack, Port, Domain, Env Vars
2. **Deployment Path** ← NEW! (optional if prefilled)
3. **Process Manager**: pm2 | systemd | docker
4. **Branch**: Which git branch to deploy
5. **GitHub Token**: Only if repo is private

## Testing

Syntax verified ✓
- `app/agents/deployment_agent.py` - No syntax errors
- `app/api/chat.py` - No syntax errors

## Files Modified
- `app/agents/deployment_agent.py` (1 block added)
- `app/api/chat.py` (1 block added)

## Backward Compatibility
✓ Fully backward compatible
✓ Existing deployments continue to work
✓ Default paths work as before if no custom path provided
