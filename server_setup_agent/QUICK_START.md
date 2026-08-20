# Quick Start Guide - Private Key Authentication

## For Frontend Developers

### 1. Read Private Key from User

```typescript
// Let user select their private key file
<input type="file" onChange={handleKeySelect} accept="id_rsa,id_ed25519,.pem,.key" />

// Read the file content
const handleKeySelect = (e: React.ChangeEvent<HTMLInputElement>) => {
  const file = e.target.files?.[0];
  if (file) {
    const reader = new FileReader();
    reader.onload = (e) => {
      const privateKey = e.target?.result as string;
      setPrivateKey(privateKey);  // Store in state
    };
    reader.readAsText(file);
  }
};
```

### 2. Encrypt and Send to Backend

```typescript
import CryptoJS from 'crypto-js';

const ENCRYPTION_KEY = 'my-super-secret-encryption-key';  // Must match backend

function encryptPayload(data: any): string {
  const jsonStr = JSON.stringify(data);
  const iv = CryptoJS.lib.WordArray.random(12);
  const key = CryptoJS.enc.Utf8.parse(ENCRYPTION_KEY.padEnd(32, '0').slice(0, 32));
  
  const encrypted = CryptoJS.AES.encrypt(jsonStr, key, {
    iv: iv,
    mode: CryptoJS.mode.GCM,
    padding: CryptoJS.pad.NoPadding
  });
  
  return CryptoJS.enc.Base64.stringify(iv.concat(encrypted.ciphertext));
}

// Send connection request
const response = await fetch('/api/v1/connect', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    encrypted_data: encryptPayload({
      host: '31.97.224.45',
      port: 2200,
      username: 'bipin',
      private_key: privateKey,  // The content you read from file
      sudo_password: 'MyPassword123'
    })
  })
});

const result = await response.json();
if (result.connected) {
  console.log('✓ Connected!');
}
```

### 3. Send Queries

```typescript
const response = await fetch('/api/v1/query', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    encrypted_data: encryptPayload({
      query: 'deploy my app',
      credentials: {
        executor_type: 'ssh',
        host: '31.97.224.45',
        username: 'bipin',
        private_key: privateKey,
        sudo_password: 'MyPassword123'
      },
      conversation_id: conversationId  // Track multi-turn conversations
    })
  })
});
```

---

## For Backend Developers

### No Code Changes Needed!

The backend automatically:
1. ✅ Detects encrypted vs plain payloads
2. ✅ Decrypts if needed
3. ✅ Parses private key content
4. ✅ Authenticates with server
5. ✅ Returns clear error messages

### Environment Setup

```bash
# .env file
ENCRYPTION_KEY=my-super-secret-encryption-key  # Must match frontend
```

---

## For System Administrators

### 1. Add User's Public Key to Server

```bash
# SSH as admin user
ssh -p 2200 admin@31.97.224.45

# Create user with SSH key
sudo adduser --disabled-password bipin
sudo mkdir -p /home/bipin/.ssh
echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICddsZ..." | sudo tee /home/bipin/.ssh/authorized_keys
sudo chmod 700 /home/bipin/.ssh
sudo chmod 600 /home/bipin/.ssh/authorized_keys
sudo chown -R bipin:bipin /home/bipin/.ssh

# Add to sudo group
sudo usermod -aG sudo bipin

# Grant passwordless sudo
echo "bipin ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/bipin
sudo chmod 440 /etc/sudoers.d/bipin
```

### 2. Verify Setup

```bash
# Check user exists
id bipin

# Check SSH key installed
sudo cat /home/bipin/.ssh/authorized_keys

# Check permissions
sudo ls -la /home/bipin/.ssh/
```

---

## For End Users

### 1. Generate SSH Key (One Time)

```bash
# On your local machine
ssh-keygen -t ed25519 -C "your-email@example.com"

# This creates:
# ~/.ssh/id_ed25519 (private key - KEEP SECRET!)
# ~/.ssh/id_ed25519.pub (public key - give to admin)
```

### 2. Give Public Key to Admin

```bash
# Display your public key
cat ~/.ssh/id_ed25519.pub

# Copy the output and send to your system administrator
```

### 3. Login via Application

1. Open the application
2. Select your private key file (`~/.ssh/id_ed25519`)
3. Enter your username
4. Enter server details (host, port)
5. Enter sudo password (if needed)
6. Click "Connect"

---

## Testing

### Test from Command Line

```bash
# Make sure backend is running
cd server_setup_agent
docker compose up -d

# Run test script
python test_private_key_auth.py
```

### Test from Browser Console

```javascript
// Paste this in browser console
const privateKey = `-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmU...
-----END OPENSSH PRIVATE KEY-----`;

fetch('/api/v1/connect', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    host: '31.97.224.45',
    port: 2200,
    username: 'bipin',
    private_key: privateKey
  })
}).then(r => r.json()).then(console.log);
```

---

## Common Issues

### ❌ "Could not parse private key"
**Fix**: Make sure you're uploading the **private key** (not the `.pub` file)

### ❌ "SSH key rejected"
**Fix**: Your private key doesn't match any public key on the server. Ask admin to install your public key.

### ❌ "Decryption failed"
**Fix**: `ENCRYPTION_KEY` mismatch between frontend and backend. Check `.env` file.

### ❌ "Connection refused"
**Fix**: Backend not running. Run `docker compose up -d`

### ❌ "User not allowed because account is locked"
**Fix**: Account was locked. SSH as admin and run: `sudo passwd username`

---

## File Checklist

**Frontend needs**:
- ✅ `crypto-js` library (`npm install crypto-js`)
- ✅ File upload input for private key
- ✅ Encryption function
- ✅ Same `ENCRYPTION_KEY` as backend

**Backend needs**:
- ✅ `cryptography` library (already in requirements.txt)
- ✅ `ENCRYPTION_KEY` in `.env`
- ✅ Backend running (`docker compose up -d`)

**Server needs**:
- ✅ User created
- ✅ Public key in `/home/username/.ssh/authorized_keys`
- ✅ Correct permissions (700 for .ssh, 600 for authorized_keys)

---

## Security Checklist

✅ **Always encrypt** sensitive payloads before sending
✅ **Use HTTPS** in production
✅ **Each user has own** private key
✅ **Never share** private keys
✅ **Keep private keys** on user's machine only
✅ **Rotate encryption keys** periodically
✅ **Monitor** failed login attempts
✅ **Use strong passwords** for sudo access

---

## That's It!

You're ready to use private key authentication. 🚀

**Questions?** Check the detailed documentation:
- `PRIVATE_KEY_AUTHENTICATION.md` - Full authentication guide
- `ENCRYPTION_SETUP.md` - Encryption setup details
- `FINAL_IMPLEMENTATION_SUMMARY.md` - Complete feature overview
