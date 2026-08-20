# Private Key Authentication from Frontend

## Overview

The backend now supports **private key content** sent directly from the frontend for SSH authentication. This is the recommended approach for proper identity management, where each user provides their own private key that matches a public key already installed on the server.

## How It Works

1. **User has key pair**: User has generated an SSH key pair on their machine
2. **Public key on server**: User's public key is already installed in `/home/username/.ssh/authorized_keys` on the server
3. **Frontend sends private key**: Frontend reads the private key content and sends it to backend (encrypted)
4. **Backend authenticates**: Backend uses the private key to SSH into the server
5. **Server verifies**: Server verifies the private key matches the public key and allows login

## Authentication Priority Order

The backend tries authentication methods in this order:

1. **`private_key`** (content string) - **HIGHEST PRIORITY** ✅ Recommended
2. **`key_filename`** (file path) - File path on backend server
3. **Auto-discovery** - Auto-find keys in `~/.ssh/` directory
4. **`password`** - Username/password authentication (fallback)

## API Request Format

### Plain JSON Request

```json
{
  "host": "31.97.224.45",
  "port": 2200,
  "username": "bipin",
  "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n...\n-----END OPENSSH PRIVATE KEY-----",
  "sudo_password": "MyPassword123"
}
```

### Encrypted Request (Recommended)

```json
{
  "encrypted_data": "base64-encoded-encrypted-payload-containing-private-key"
}
```

## Frontend Implementation

### Reading Private Key File

```typescript
// React/TypeScript Example
import { useState } from 'react';

function SSHKeyUploader() {
  const [privateKey, setPrivateKey] = useState<string>('');
  
  const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) {
      const reader = new FileReader();
      reader.onload = (e) => {
        const content = e.target?.result as string;
        setPrivateKey(content);
      };
      reader.readAsText(file);
    }
  };
  
  return (
    <div>
      <input
        type="file"
        accept=".pem,.key,id_rsa,id_ed25519,id_ecdsa,id_dsa"
        onChange={handleFileSelect}
      />
      <p>Selected key: {privateKey ? 'Loaded' : 'None'}</p>
    </div>
  );
}
```

### Sending to Backend

```typescript
import CryptoJS from 'crypto-js';

const ENCRYPTION_KEY = process.env.REACT_APP_ENCRYPTION_KEY || 'my-super-secret-encryption-key';

function encryptPayload(data: any): string {
  const jsonStr = JSON.stringify(data);
  const iv = CryptoJS.lib.WordArray.random(12);
  const key = CryptoJS.enc.Utf8.parse(ENCRYPTION_KEY.padEnd(32, '0').slice(0, 32));
  
  const encrypted = CryptoJS.AES.encrypt(jsonStr, key, {
    iv: iv,
    mode: CryptoJS.mode.GCM,
    padding: CryptoJS.pad.NoPadding
  });
  
  const combined = iv.concat(encrypted.ciphertext);
  return CryptoJS.enc.Base64.stringify(combined);
}

async function connectToServer(
  host: string,
  username: string,
  privateKey: string,
  sudoPassword: string
) {
  const payload = {
    host: host,
    port: 2200,
    username: username,
    private_key: privateKey,  // Private key content as string
    sudo_password: sudoPassword
  };
  
  // Encrypt the payload
  const encryptedData = encryptPayload(payload);
  
  // Send to backend
  const response = await fetch('/api/v1/connect', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ encrypted_data: encryptedData })
  });
  
  return response.json();
}
```

### Complete Form Example

```typescript
import React, { useState } from 'react';

function ConnectionForm() {
  const [host, setHost] = useState('31.97.224.45');
  const [port, setPort] = useState(2200);
  const [username, setUsername] = useState('');
  const [privateKey, setPrivateKey] = useState('');
  const [sudoPassword, setSudoPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  
  const handleKeyFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) {
      const reader = new FileReader();
      reader.onload = (e) => {
        const content = e.target?.result as string;
        setPrivateKey(content);
      };
      reader.readAsText(file);
    }
  };
  
  const handleConnect = async () => {
    setLoading(true);
    try {
      const result = await connectToServer(host, username, privateKey, sudoPassword);
      setResult(result);
      
      if (result.connected) {
        console.log('✓ Connected successfully!');
      } else {
        console.error('✗ Connection failed:', result.error);
      }
    } catch (error) {
      console.error('✗ Error:', error);
      setResult({ connected: false, error: String(error) });
    } finally {
      setLoading(false);
    }
  };
  
  return (
    <div className="connection-form">
      <h2>Connect to Server</h2>
      
      <div>
        <label>Host:</label>
        <input
          type="text"
          value={host}
          onChange={(e) => setHost(e.target.value)}
          placeholder="31.97.224.45"
        />
      </div>
      
      <div>
        <label>Port:</label>
        <input
          type="number"
          value={port}
          onChange={(e) => setPort(Number(e.target.value))}
          placeholder="2200"
        />
      </div>
      
      <div>
        <label>Username:</label>
        <input
          type="text"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="bipin"
        />
      </div>
      
      <div>
        <label>Private SSH Key:</label>
        <input
          type="file"
          accept=".pem,.key,id_rsa,id_ed25519,id_ecdsa,id_dsa"
          onChange={handleKeyFileSelect}
        />
        {privateKey && <p>✓ Key loaded ({privateKey.length} bytes)</p>}
      </div>
      
      <div>
        <label>Sudo Password:</label>
        <input
          type="password"
          value={sudoPassword}
          onChange={(e) => setSudoPassword(e.target.value)}
          placeholder="Enter sudo password"
        />
      </div>
      
      <button
        onClick={handleConnect}
        disabled={loading || !host || !username || !privateKey}
      >
        {loading ? 'Connecting...' : 'Connect'}
      </button>
      
      {result && (
        <div className={result.connected ? 'success' : 'error'}>
          {result.connected ? (
            <p>✓ Connected as {result.user}@{result.host}</p>
          ) : (
            <p>✗ Error: {result.error}</p>
          )}
        </div>
      )}
    </div>
  );
}

export default ConnectionForm;
```

## Backend Implementation

### Supported Key Formats

The backend automatically detects and supports:

- ✅ **Ed25519** keys (`id_ed25519`)
- ✅ **RSA** keys (`id_rsa`)
- ✅ **ECDSA** keys (`id_ecdsa`)
- ✅ **DSA** keys (`id_dsa`)

### Key Format Examples

**OpenSSH Format** (recommended):
```
-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW
QyNTUxOQAAACBbWp...
-----END OPENSSH PRIVATE KEY-----
```

**PEM Format**:
```
-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA2...
-----END RSA PRIVATE KEY-----
```

### Error Messages

The backend provides clear error messages:

| Error | Meaning | Solution |
|-------|---------|----------|
| "Invalid private key: Could not parse" | Private key format is invalid | Check key format, ensure it's complete |
| "SSH key rejected. The private key doesn't match..." | Private key doesn't match any public key on server | Verify correct key is being used |
| "Server only accepts SSH key authentication..." | Server requires keys, password not allowed | Provide a private key instead of password |

## Security Considerations

### ✅ Best Practices

1. **Always Use Encryption**: Encrypt private keys before sending over network
2. **Use HTTPS**: Always use HTTPS in production (encryption is not a substitute for HTTPS)
3. **Never Log Private Keys**: Backend should never log private key content
4. **Memory Cleanup**: Private keys should be cleared from memory after use
5. **User-Specific Keys**: Each user should use their own private key

### ❌ What NOT to Do

1. ❌ Store private keys in databases
2. ❌ Send private keys over unencrypted connections
3. ❌ Share private keys between users
4. ❌ Commit private keys to version control
5. ❌ Use the same key for all environments

## Testing

### Test with `curl`

```bash
# Read private key
PRIVATE_KEY=$(cat ~/.ssh/id_ed25519)

# Create JSON payload
cat > payload.json <<EOF
{
  "host": "31.97.224.45",
  "port": 2200,
  "username": "bipin",
  "private_key": "$PRIVATE_KEY",
  "sudo_password": "MyPassword123"
}
EOF

# Test connection
curl -X POST http://localhost:8000/api/v1/connect \
  -H "Content-Type: application/json" \
  -d @payload.json
```

### Test with Python

```python
from app.utils.encryption import encrypt_payload
import requests

# Read private key
with open('/path/to/private/key', 'r') as f:
    private_key = f.read()

# Create payload
payload = {
    "host": "31.97.224.45",
    "port": 2200,
    "username": "bipin",
    "private_key": private_key,
    "sudo_password": "MyPassword123"
}

# Encrypt
encrypted = encrypt_payload(payload, "my-super-secret-encryption-key")

# Send request
response = requests.post('http://localhost:8000/api/v1/connect', json={
    'encrypted_data': encrypted
})

print(response.json())
```

## Complete Flow Example

### 1. User Generates Key Pair (One Time)

```bash
# On user's machine
ssh-keygen -t ed25519 -C "bipin@mycompany"
# Creates: ~/.ssh/id_ed25519 (private) and ~/.ssh/id_ed25519.pub (public)
```

### 2. Admin Installs Public Key on Server (One Time)

```bash
# Admin adds user's public key to server
# Via the application or manually:
sudo mkdir -p /home/bipin/.ssh
echo "ssh-ed25519 AAAAC3NzaC..." | sudo tee -a /home/bipin/.ssh/authorized_keys
sudo chmod 700 /home/bipin/.ssh
sudo chmod 600 /home/bipin/.ssh/authorized_keys
sudo chown -R bipin:bipin /home/bipin/.ssh
```

### 3. User Connects via Frontend (Every Time)

1. User opens the application
2. User selects their private key file (`~/.ssh/id_ed25519`)
3. Frontend reads the private key content
4. Frontend encrypts the payload including private key
5. Frontend sends to backend
6. Backend decrypts payload
7. Backend uses private key to SSH into server
8. Server verifies private key matches public key
9. ✓ User is logged in!

## Comparison: Different Authentication Methods

| Method | Security | Convenience | Recommended |
|--------|----------|-------------|-------------|
| **Private key from frontend** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ✅ **YES** |
| Key file path on backend | ⭐⭐⭐ | ⭐⭐ | ⚠️ Only for system keys |
| Auto-discovery | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⚠️ Only for development |
| Password | ⭐⭐ | ⭐⭐⭐⭐⭐ | ❌ Avoid in production |

## Files Modified

- ✅ `app/executors/ssh_executor.py` - Added `private_key` parameter and parsing logic
- ✅ `app/api/connect.py` - Added `private_key` field to `ConnectRequest`
- ✅ `app/api/schemas.py` - Added `private_key` field to `ServerCredentials`
- ✅ `app/executors/executor_factory.py` - Already supports **kwargs (no change needed)

## Summary

✅ **Backend now supports private key content from frontend**
✅ **Proper identity management - each user uses their own key**
✅ **Secure authentication - private key matches public key on server**
✅ **Encrypted transmission - sensitive data is encrypted**
✅ **Clear error messages - easy to debug authentication issues**
✅ **Multiple key formats supported - Ed25519, RSA, ECDSA, DSA**
✅ **Backward compatible - old authentication methods still work**

This is the **recommended approach** for production deployments where users need to authenticate with their own credentials!
