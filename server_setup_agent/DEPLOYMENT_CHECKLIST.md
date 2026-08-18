# Deployment Checklist - GitHub Authentication Fix

## Pre-Deployment

- [ ] Read `GITHUB_AUTH_FIX.md` (technical details)
- [ ] Read `AUTHENTICATION_FIX_SUMMARY.md` (overview)
- [ ] Understand the 3 changes being made
- [ ] Have backup of current `app/agents/deployment_agent.py`

## Code Changes

- [ ] **Change 1**: Add `_validate_github_token()` method (lines ~167-190)
  - Validates token using GitHub API
  - Returns True/False
  
- [ ] **Change 2**: Update `_complete_context()` (lines ~410-450)
  - Call `_validate_github_token()` before proceeding
  - Provide helpful error if invalid
  
- [ ] **Change 3**: Fix `_step_clone()` (lines ~540-580)
  - Replace `--config credential.helper=''` with `GIT_ASKPASS=echo`
  - Add try-catch for auth errors

## Verification

- [ ] Syntax check passed:
  ```bash
  python -m py_compile app/agents/deployment_agent.py
  ```

- [ ] No import errors
- [ ] All method calls are valid
- [ ] No typos in new methods

## Testing

### Test 1: Valid Public Repo
```bash
curl -X POST http://localhost:8000/api/query \
  -d '{"query": "deploy https://github.com/owner/public-repo react 3000"}'
```
**Expected**: Deployment proceeds without token

- [ ] ✓ Test passed

### Test 2: Valid Private Repo with Valid Token
```bash
curl -X POST http://localhost:8000/api/query \
  -d '{
    "query": "deploy https://github.com/owner/private-repo react 3000",
    "deployment_params": {"github_token": "ghp_valid_token"}
  }'
```
**Expected**: Token validated, deployment proceeds

- [ ] ✓ Test passed

### Test 3: Private Repo with Invalid Token
```bash
curl -X POST http://localhost:8000/api/query \
  -d '{
    "query": "deploy https://github.com/owner/private-repo react 3000",
    "deployment_params": {"github_token": "ghp_invalid"}
  }'
```
**Expected**: needs_input=true with message asking for valid token

- [ ] ✓ Test passed

### Test 4: Headless Environment
Deploy from SSH/remote machine without TTY
**Expected**: Clone completes without TTY prompt errors

- [ ] ✓ Test passed

## Deployment Steps

1. [ ] Backup current file:
   ```bash
   cp app/agents/deployment_agent.py app/agents/deployment_agent.py.backup
   ```

2. [ ] Apply the 3 changes to `app/agents/deployment_agent.py`

3. [ ] Verify syntax:
   ```bash
   python -m py_compile app/agents/deployment_agent.py
   echo $?  # Should output: 0
   ```

4. [ ] Restart service:
   ```bash
   systemctl restart server_setup_agent
   # or
   pm2 restart server_setup_agent
   ```

5. [ ] Verify service is running:
   ```bash
   systemctl status server_setup_agent
   # or
   pm2 list | grep server_setup_agent
   ```

6. [ ] Check logs for errors:
   ```bash
   pm2 logs server_setup_agent | tail -50
   ```

## Post-Deployment

- [ ] Monitor logs for 30 minutes
- [ ] No errors in logs
- [ ] All deployments proceeding normally
- [ ] Authentication working for valid tokens
- [ ] Error messages are clear for invalid tokens

## Rollback Plan (If Issues)

If deployment has problems:

1. [ ] Stop service:
   ```bash
   systemctl stop server_setup_agent
   ```

2. [ ] Restore backup:
   ```bash
   cp app/agents/deployment_agent.py.backup app/agents/deployment_agent.py
   ```

3. [ ] Verify syntax:
   ```bash
   python -m py_compile app/agents/deployment_agent.py
   ```

4. [ ] Start service:
   ```bash
   systemctl start server_setup_agent
   ```

5. [ ] Verify it's running:
   ```bash
   systemctl status server_setup_agent
   ```

## Documentation for Users

Share with users:
- [ ] `GITHUB_TOKEN_TROUBLESHOOTING.md`
- [ ] How to create GitHub token
- [ ] Common errors and solutions

## Communication

- [ ] Inform team of changes
- [ ] Document in release notes
- [ ] Update internal wiki/docs
- [ ] Schedule team briefing if needed

## Sign-Off

| Role | Name | Date | Approved |
|------|------|------|----------|
| Developer | [NAME] | [DATE] | ☐ |
| Reviewer | [NAME] | [DATE] | ☐ |
| QA | [NAME] | [DATE] | ☐ |
| DevOps | [NAME] | [DATE] | ☐ |

## Notes

```
[Space for deployment notes, issues encountered, etc.]


```

---

## Quick Reference

### The 3 Changes
1. New method: `_validate_github_token()`
2. Updated: Token validation in `_complete_context()`
3. Fixed: Git clone command with `GIT_ASKPASS=echo`

### Files Modified
- `app/agents/deployment_agent.py` (1 file, 3 changes)

### Files NOT Modified
- No other files need changes
- No configuration files
- No database migrations

### Estimated Time
- Backup: 1 minute
- Apply changes: 5 minutes
- Testing: 10-15 minutes
- Restart: 2 minutes
- **Total: ~25 minutes**

### Risk Level
**LOW** - Backward compatible changes only

### Rollback Difficulty
**EASY** - Single file backup restore

---

## Success Indicators

✅ Deployments proceed normally
✅ Invalid tokens are caught with clear messages
✅ Headless servers don't get TTY errors
✅ Public repos don't require tokens
✅ Token validation happens before clone
✅ Error messages guide users to solutions

---

## Support Contacts

For issues during deployment:

- **Technical**: [DevOps Lead]
- **Questions**: [Kiro Support]
- **Escalation**: [Engineering Lead]

---

**Deployment Date**: ________________
**Completed By**: ________________
**Sign-Off**: ________________

---

## Final Checklist Before Production

- [ ] All tests passed
- [ ] Syntax verified
- [ ] Backups created
- [ ] Documentation shared
- [ ] Team notified
- [ ] Rollback plan ready
- [ ] Monitoring set up
- [ ] Success criteria understood

✅ **Ready for Production Deployment**

---

**Last Updated**: 2026-08-17
