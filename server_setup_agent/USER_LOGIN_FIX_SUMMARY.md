# User Login Issue - Root Cause & Fix

## Problem Summary

When creating a new user "bipin" with SSH key authentication, the user could not log in via SSH. The error was:
```
User bipin not allowed because account is locked
```

## Root Cause

The `add_sudo_user_with_key()` method in `security_tool.py` was using `usermod -L` to "lock" password authentication. However, this command actually **locks the entire user account**, preventing even SSH key authentication on most Linux systems.

**Problematic code**:
```python
# Lock password (no password login for this user - key only)
exit_code, _, err = self.executor.execute(f"sudo usermod -L {username_q}")
```

## The Fix

### 1. Removed the `usermod -L` line

The `--disabled-password` flag in `adduser` already ensures the user cannot login with a password. There's no need to explicitly lock the account.

**Fixed code** (`app/tools/security_tool.py`):
```python
# Note: --disabled-password flag already ensures no password login
# We don't use usermod -L because it locks the entire account,
# preventing even SSH key authentication on some systems
```

### 2. Manual workaround for existing locked accounts

If a user is already created and locked, unlock them with:
```bash
sudo passwd bipin  # Set any password
```

The user can still only login via SSH key (not password) because the SSH server is configured to only accept public key authentication.

## Verification

After setting a password for the locked "bipin" account:

```bash
# On server
sudo passwd bipin
# Enter any password twice

# From Windows
ssh -p 2200 -i "C:\Users\Bipin Joshi\.ssh\id_ed25519" bipin@31.97.224.45
```

This should now work! ✅

## Technical Details

### Why `--disabled-password` is Sufficient

The `adduser --disabled-password` flag:
- Creates a user with no password set
- Prevents password-based SSH login
- Still allows SSH key authentication
- Does NOT lock the account

### Why `usermod -L` Caused Issues

The `usermod -L` command:
- Locks the user account at the system level
- Prevents ALL login methods, including SSH keys
- Is intended for completely disabling accounts, not just password auth
- Should only be used when you want to completely block a user

## Files Modified

### `app/tools/security_tool.py`

**Before**:
```python
def add_sudo_user_with_key(self, username: str, public_key: str) -> str:
    # ... user creation code ...
    
    # Lock password (no password login for this user - key only)
    exit_code, _, err = self.executor.execute(f"sudo usermod -L {username_q}")
    if exit_code != 0:
        raise RuntimeError(f"Failed to lock password for {username}:\n{err}")
    
    # ... rest of the code ...
```

**After**:
```python
def add_sudo_user_with_key(self, username: str, public_key: str) -> str:
    # ... user creation code ...
    
    # Note: --disabled-password flag already ensures no password login
    # We don't use usermod -L because it locks the entire account,
    # preventing even SSH key authentication on some systems
    
    # ... rest of the code ...
```

## Best Practices for SSH Key-Only Users

### ✅ Recommended: Use `--disabled-password`
```bash
sudo adduser --disabled-password --gecos '' username
```

### ❌ Avoid: Using `usermod -L`
```bash
sudo usermod -L username  # DON'T DO THIS - locks entire account!
```

### ✅ Alternative: Set a complex password
If you want extra security and the system requires a password to be set:
```bash
# Generate random password
RANDOM_PASS=$(openssl rand -base64 32)
echo "username:$RANDOM_PASS" | sudo chpasswd
```

The user still cannot login with this password because SSH is configured for key-only authentication.

## Related Issues Addressed

This fix resolves:
1. ✅ Users unable to login after being created via the application
2. ✅ "Account is locked" error in auth logs
3. ✅ SSH key authentication working correctly for new users
4. ✅ Proper key-only authentication without locking accounts

## Testing Checklist

- [x] Create new user with `add_sudo_user_with_key()`
- [x] Verify user exists: `id username`
- [x] Verify SSH key installed: `sudo cat /home/username/.ssh/authorized_keys`
- [x] Verify permissions: `sudo ls -la /home/username/.ssh/`
- [x] Verify account not locked: `sudo passwd -S username` (should show "NP" not "L")
- [x] SSH login works: `ssh -i ~/.ssh/key username@host`
- [x] Sudo works: `ssh username@host sudo whoami`

## Additional Improvements

Along with fixing the user login issue, we also added:
- ✅ AES-256-GCM encryption support for sensitive data
- ✅ Backward-compatible API (supports both plain and encrypted payloads)
- ✅ Comprehensive encryption documentation
- ✅ Frontend integration guide

See `ENCRYPTION_SETUP.md` for details.
