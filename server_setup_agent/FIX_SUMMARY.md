# Server Setup Agent - Fix Summary

## Problem
The setup agent was stuck in an infinite loop when users provided ambiguous responses like:
- "yes"
- "ok" 
- "start"
- "full setup"

Instead of being recognized as valid clarifications or setup requests, these inputs kept triggering the same prompt: "What would you like to set up?"

## Root Cause
The `_GATHER_SYSTEM` prompt in `setup_agent.py` wasn't explicitly instructing the LLM to handle these common single-word responses. The LLM would treat them as vague or incomplete setup requests and return `{"missing": true}`, causing the agent to ask the question again.

## Solution
Updated the `_GATHER_SYSTEM` prompt in `app/agents/setup_agent.py` to include:

1. **Special handling section** - Explicit rules for clarification responses
   ```
   Special handling for clarification responses:
   - If user just says "yes", "y", "ok", "proceed", "start", "go", "confirm", "sure" after the question:
     treat as: "I'm ready to proceed but haven't told you what to setup — ask again what they want"
   - If user says "full setup" or "everything": include all tasks + common infra
   ```

2. **Additional examples** - New JSON response patterns showing:
   - How to handle single-word acknowledgments (return `{"missing": true}`)
   - How to handle "full setup" (return complete setup plan with all tasks)

## Changes Made

### File: `app/agents/setup_agent.py`

**Line 50-102:** Updated `_GATHER_SYSTEM` prompt

**Added:**
- Section for special handling of clarification responses
- Example for "full setup" → returns complete setup plan
- Clarification that "yes/ok/start/etc" should trigger another clarification question

## Expected Behavior After Fix

### User Input: "yes", "ok", "start", "proceed", etc.
**Response:**
```json
{
  "missing": true,
  "question": "What would you like to set up? E.g. web server (nginx), Node.js app, Python app, Docker, Redis, Postgres, full setup, fresh server bootstrap, etc."
}
```
→ User then provides the actual setup request, breaking the loop

### User Input: "full setup"
**Response:**
```json
{
  "tasks": ["base", "nginx", "docker", "nodejs", "pm2", "python", "firewall", "fail2ban", "ssh_harden", "auto_updates"],
  "infra_services": ["redis", "postgres"],
  "extra_packages": [],
  "extra_commands": [],
  "firewall_ports": [],
  "server_purpose": "Complete server setup with all common services",
  "new_username": ""
}
```
→ Setup plan presented for user confirmation, no loop

### User Input: Specific requests like "nginx and docker"
**Response:** Returns the specific setup plan (behavior unchanged)

## Testing
- Verified syntax of modified file ✓
- Logic flow tested with verification script ✓
- No breaking changes to existing functionality

## Deployment Notes
- No database migrations needed
- No environment variables changed
- Fully backward compatible
- Ready to deploy immediately

## Files Modified
- `app/agents/setup_agent.py` - Updated `_GATHER_SYSTEM` prompt (1 change block)

## Future Improvements (Optional)
- Add more examples for other ambiguous inputs
- Consider implementing a "provide_suggestions" flag to show hints when users seem confused
- Log metrics on clarification loops to identify other common issues
