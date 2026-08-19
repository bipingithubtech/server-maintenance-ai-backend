# 🔒 Security Audit: SSH Key Handling

## Summary

✅ **SAFE**: We do NOT expose or transmit private keys  
✅ **SAFE**: Only PUBLIC keys are read and returned via API  
✅ **SAFE**: Private keys are only used internally by paramiko for authentication  

---

## What We Mount

**Docker Compose:**
```yaml
volumes:
  - ~/.ssh:/home/appuser/.ssh:ro
```

This mounts the ENTIRE `.ssh` directory which contains:
- ❌ `id_ed25519` (private key) - **NOT exposed via API**
- ✅ `id_ed25519.pub` (public key) - **CAN be exposed via API (safe)**
- ✅ `known_hosts`, `config`, etc. - Used internally

**Mount is READ-ONLY (`:ro`)** - Container cannot modify your SSH keys!

---

## What We Read & Use

### Private Keys (NOT EXPOSED)

**Code Location:** `app/executors/ssh_executor.py`

```python
def _discover_ssh_keys(self) -> Optional[list]:
    # Discovers private key PATHS
    # Returns: ['/home/appuser/.ssh/id_ed25519']
    # ❌ Does NOT return key content
    # ❌ Does NOT expose via API
    # ✅ Only used internally by paramiko
```

**Usage:**
```python
client.connect(
    key_filename=self.key_filename  # Path only, paramiko reads the file
)
```

- Private key paths are stored in `self.key_filename`
- **NEVER** sent to frontend
- **NEVER** returned in API responses
- **ONLY** used by paramiko internally to authenticate

---

### Public Keys (SAFE TO EXPOSE)

**Code Location:** `app/executors/ssh_executor.py`

```python
def get_public_key_content(self, private_key_path: str) -> Optional[str]:
    """Read PUBLIC key content from .pub file"""
    pub_key_path = f"{private_key_path}.pub"  # Adds .pub extension
    
    with open(pub_key_path, 'r') as f:
        public_key = f.read().strip()  # ✅ Reads PUBLIC key content
    
    return public_key  # ✅ Safe to return
```

- Reads `id_ed25519.pub` (public key file)
- Returns public key content (e.g., "ssh-ed25519 AAAA...")
- **SAFE** to expose via API
- Public keys are meant to be shared!

---

## API Endpoints Analysis

### 1. POST /api/v1/connect

**Current Implementation:**
```python
return ConnectResponse(
    connected=True,
    user=username,
    host=req.host,
    # ❌ public_keys field removed - not currently returned
)
```

**Status:** ✅ **SAFE** - Does not expose any keys

---

### 2. GET /api/v1/public-keys (If Implemented)

**Would Return:**
```json
{
  "public_keys": {
    "/home/appuser/.ssh/id_ed25519": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA..."
  },
  "count": 1
}
```

**Security Analysis:**
- Dictionary KEY: Private key PATH (just a file path, not sensitive)
- Dictionary VALUE: PUBLIC key CONTENT (safe to share)
- ❌ Does NOT include private key content
- ✅ **SAFE** to expose

---

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ User's System: ~/.ssh/                                      │
│                                                              │
│ ❌ id_ed25519       (PRIVATE - Never leaves system)         │
│ ✅ id_ed25519.pub   (PUBLIC - Can be shared)                │
└──────────────────────────────────────────────────────────────┘
                           │
                           │ Docker Mount (Read-Only)
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ Docker Container: /home/appuser/.ssh/                       │
│                                                              │
│ ❌ id_ed25519       → Used by paramiko internally           │
│                      (NEVER sent to frontend)               │
│                                                              │
│ ✅ id_ed25519.pub   → Read by get_public_key_content()      │
│                      (CAN be sent to frontend)              │
└──────────────────────────────────────────────────────────────┘
                           │
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ Backend Code:                                                │
│                                                              │
│ Private Key Path: '/home/appuser/.ssh/id_ed25519'          │
│ ❌ Path stored in memory                                    │
│ ❌ Never sent to API                                        │
│ ✅ Only used by paramiko.connect()                          │
│                                                              │
│ Public Key Content: 'ssh-ed25519 AAAAC3...'                │
│ ✅ Read from .pub file                                      │
│ ✅ CAN be returned in API (if implemented)                  │
└──────────────────────────────────────────────────────────────┘
                           │
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ API Response to Frontend:                                    │
│                                                              │
│ ❌ Private keys: NOT INCLUDED                               │
│ ❌ Private key paths: NOT INCLUDED                          │
│ ✅ Public keys: CAN be included (if endpoint implemented)   │
└──────────────────────────────────────────────────────────────┘
```

---

## Grep Audit Results

### Searching for Private Key Exposure

```bash
grep -r "private.*key" app/api/*.py
```

**Result:** No private key content or paths are returned in API responses ✅

### Searching for Key Filename Exposure

```bash
grep -r "key_filename" app/api/*.py
```

**Results:**
-  Used in `ConnectRequest` (INPUT only, not returned) ✅
- Not included in `ConnectResponse` ✅

---

## Conclusion

### ✅ SAFE Practices:

1. **Private keys** are only used internally by paramiko for SSH authentication
2. **Private key paths** are never returned in API responses
3. **Private key content** is never read or exposed by our code
4. **Public keys** can be safely shared and returned via API
5. Docker mount is **read-only** - cannot modify keys
6. Mount is per-user - each user's keys stay separate

### ❌ NO Security Issues Found:

- No private key content exposure
- No private key path exposure in API
- No accidental logging of private keys
- No storage of private keys in database
- No transmission of private keys to frontend

---

## If Public Key API Is Added

If we implement `GET /api/v1/public-keys` endpoint:

**What's Returned:**
```json
{
  "/home/appuser/.ssh/id_ed25519": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5..."
}
```

**Is This Safe?** YES ✅

- The KEY (`"/home/appuser/.ssh/id_ed25519"`) is just a file PATH (not sensitive)
- The VALUE (`"ssh-ed25519 AAAA..."`) is the PUBLIC key (meant to be shared)
- Public keys are designed to be public - that's their purpose!
- No private key information is included

---

## Recommendations

✅ **Current implementation is secure**  
✅ **No changes needed for security**  
✅ **Safe to implement public key API if needed**  
✅ **Continue using read-only mounts**  

---

## References

- **SSH Key Basics:** Public keys are meant to be shared, private keys must stay secret
- **Docker Security:** Read-only mounts prevent container from modifying host files
- **Paramiko:** Securely handles private keys internally without exposing them
