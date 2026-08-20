# Encryption Setup Guide

## Overview

The backend now supports AES-256-GCM encryption for sensitive data transmitted between the frontend and backend. This ensures that credentials (passwords, SSH keys, sudo passwords) are encrypted in transit, even over HTTPS.

## How It Works

1. **Frontend**: Encrypts sensitive payloads using AES-GCM before sending to backend
2. **Backend**: Decrypts payloads using the shared encryption key
3. **Encryption Key**: Shared secret key stored in environment variables (must match on both sides)

## Backend Setup

### 1. Install Required Dependency

The `cryptography` library is required for AES-GCM decryption:

```bash
pip install cryptography
```

This is already included in `requirements.txt`.

### 2. Set Encryption Key

Add the encryption key to your `.env` file:

```bash
# Encryption key for frontend-backend communication (AES-256)
# IMPORTANT: Change this to a strong random key in production!
# Must match the key used in the frontend
ENCRYPTION_KEY=my-super-secret-encryption-key
```

**⚠️ IMPORTANT**: 
- Change this to a strong, random key in production
- Keep this key secret and never commit it to version control
- The same key must be used in both frontend and backend
- Generate a secure key: `openssl rand -base64 32`

### 3. API Endpoints Support Both Plain and Encrypted Payloads

The following endpoints now support both plain JSON and encrypted payloads:

#### `/api/v1/connect`

**Plain Request**:
```json
{
  "host": "31.97.224.45",
  "port": 2200,
  "username": "meetri",
  "password": "MyPassword123",
  "sudo_password": "MyPassword123"
}
```

**Encrypted Request**:
```json
{
  "encrypted_data": "base64-encoded-encrypted-payload"
}
```

#### `/api/v1/query`

**Plain Request**:
```json
{
  "query": "deploy my app",
  "credentials": {
    "executor_type": "ssh",
    "host": "31.97.224.45",
    "username": "meetri",
    "password": "MyPassword123",
    "sudo_password": "MyPassword123"
  },
  "conversation_id": "abc-123"
}
```

**Encrypted Request**:
```json
{
  "encrypted_data": "base64-encoded-encrypted-payload"
}
```

### 4. How Decryption Works

The backend automatically detects if a payload is encrypted:

1. **Check for `encrypted_data` field**: If present, decrypt it
2. **Decrypt using AES-GCM**: Extract IV (first 12 bytes) and decrypt the rest
3. **Parse JSON**: Convert decrypted bytes to JSON
4. **Process request**: Handle as normal request

Code example:
```python
from app.utils.encryption import decrypt_payload

decrypted_data = decrypt_payload(encrypted_payload, encryption_key)
# Returns: {'host': '31.97.224.45', 'username': 'meetri', ...}
```

## Frontend Setup

The frontend must encrypt sensitive payloads before sending to the backend.

### Example Frontend Code (React/TypeScript)

```typescript
import CryptoJS from 'crypto-js';

const ENCRYPTION_KEY = 'my-super-secret-encryption-key';

function encryptPayload(data: any): string {
  // Convert to JSON
  const jsonStr = JSON.stringify(data);
  
  // Generate random IV (12 bytes for GCM)
  const iv = CryptoJS.lib.WordArray.random(12);
  
  // Pad key to 32 bytes (256 bits)
  const key = CryptoJS.enc.Utf8.parse(ENCRYPTION_KEY.padEnd(32, '0').slice(0, 32));
  
  // Encrypt using AES-GCM
  const encrypted = CryptoJS.AES.encrypt(jsonStr, key, {
    iv: iv,
    mode: CryptoJS.mode.GCM,
    padding: CryptoJS.pad.NoPadding
  });
  
  // Combine IV + encrypted data
  const combined = iv.concat(encrypted.ciphertext);
  
  // Encode as base64
  return CryptoJS.enc.Base64.stringify(combined);
}

// Usage
const connectionData = {
  host: '31.97.224.45',
  port: 2200,
  username: 'meetri',
  password: 'MyPassword123',
  sudo_password: 'MyPassword123'
};

const encryptedData = encryptPayload(connectionData);

// Send to backend
fetch('/api/v1/connect', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ encrypted_data: encryptedData })
});
```

## Testing

### Test with Plain Payload (Still Supported)

```bash
curl -X POST http://localhost:8000/api/v1/connect \
  -H "Content-Type: application/json" \
  -d '{
    "host": "31.97.224.45",
    "port": 2200,
    "username": "meetri",
    "password": "MyPassword123"
  }'
```

### Test with Encrypted Payload

```python
# test_encryption.py
from app.utils.encryption import encrypt_payload, decrypt_payload

# Encrypt
data = {
    "host": "31.97.224.45",
    "port": 2200,
    "username": "meetri",
    "password": "MyPassword123"
}

encryption_key = "my-super-secret-encryption-key"
encrypted = encrypt_payload(data, encryption_key)
print(f"Encrypted: {encrypted}")

# Decrypt
decrypted = decrypt_payload(encrypted, encryption_key)
print(f"Decrypted: {decrypted}")

# Test API
import requests
response = requests.post('http://localhost:8000/api/v1/connect', json={
    'encrypted_data': encrypted
})
print(response.json())
```

## Security Best Practices

1. **Use HTTPS**: Always use HTTPS in production to prevent man-in-the-middle attacks
2. **Rotate Keys**: Periodically rotate the encryption key
3. **Strong Keys**: Use cryptographically secure random keys (at least 32 characters)
4. **Environment Variables**: Never hardcode keys in source code
5. **Key Management**: Use secure key management systems (AWS KMS, Azure Key Vault, etc.) in production
6. **Separate Keys**: Use different keys for development, staging, and production

## Troubleshooting

### "Decryption failed" Error

**Cause**: Encryption key mismatch between frontend and backend

**Solution**: 
1. Verify the `ENCRYPTION_KEY` in `.env` matches the frontend key exactly
2. Check for extra spaces or newlines in the key
3. Ensure both sides are using the same key padding logic

### "Invalid request format" Error

**Cause**: Payload is neither plain JSON nor encrypted format

**Solution**:
1. Check that the request has either the expected fields OR an `encrypted_data` field
2. Verify JSON is valid before encryption
3. Check base64 encoding is correct

### Encryption Works Locally But Not in Production

**Cause**: Environment variable not set in production

**Solution**:
1. Verify `ENCRYPTION_KEY` is set in production environment
2. Check docker-compose.yml or deployment config includes the env variable
3. Restart the backend service after adding the variable

## Migration from Plain to Encrypted

If you're migrating from plain JSON to encrypted payloads:

1. **Backend supports both**: No backend changes needed - it auto-detects
2. **Update frontend gradually**: Deploy encrypted frontend when ready
3. **No breaking changes**: Old clients continue to work with plain JSON
4. **Force encryption**: Add a config flag to reject plain payloads if needed

## Files Modified

- `app/utils/encryption.py` - New encryption/decryption helper
- `app/api/connect.py` - Updated to support encrypted payloads
- `app/api/chat.py` - Updated to support encrypted payloads
- `app/api/schemas.py` - Added `EncryptedRequest` model
- `.env` - Added `ENCRYPTION_KEY` configuration

## Summary

✅ **Fixed**: `usermod -L` bug in `security_tool.py` that locked accounts
✅ **Added**: AES-256-GCM encryption/decryption support
✅ **Added**: Encryption helper utility (`app/utils/encryption.py`)
✅ **Updated**: `/api/v1/connect` to support encrypted payloads
✅ **Updated**: `/api/v1/query` to support encrypted payloads
✅ **Added**: `ENCRYPTION_KEY` environment variable
✅ **Backward Compatible**: Plain JSON payloads still work
