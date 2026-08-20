"""
Test script for private key authentication
==========================================

This script tests the private key authentication feature by:
1. Reading a private key from file
2. Creating a connection request with private key content
3. Sending to the backend API
4. Verifying successful authentication

Usage:
    python test_private_key_auth.py
"""

import os
import sys

def test_private_key_auth():
    """Test private key authentication"""
    import requests
    
    # Configuration
    BACKEND_URL = os.getenv('BACKEND_URL', 'http://localhost:8000')
    PRIVATE_KEY_PATH = os.path.expanduser('~/.ssh/id_ed25519')
    HOST = '31.97.224.45'
    PORT = 2200
    USERNAME = 'bipin'
    SUDO_PASSWORD = 'MyPassword123'  # Change this
    
    print("=" * 60)
    print("Testing Private Key Authentication")
    print("=" * 60)
    
    # Step 1: Read private key
    print(f"\n1. Reading private key from: {PRIVATE_KEY_PATH}")
    if not os.path.exists(PRIVATE_KEY_PATH):
        print(f"✗ Error: Private key not found at {PRIVATE_KEY_PATH}")
        print(f"   Please specify the correct path or generate a key:")
        print(f"   ssh-keygen -t ed25519 -C 'your-email@example.com'")
        return False
    
    with open(PRIVATE_KEY_PATH, 'r') as f:
        private_key = f.read()
    
    print(f"✓ Private key loaded ({len(private_key)} bytes)")
    print(f"  First line: {private_key.split(chr(10))[0]}")
    
    # Step 2: Create payload
    print(f"\n2. Creating connection request")
    payload = {
        "host": HOST,
        "port": PORT,
        "username": USERNAME,
        "private_key": private_key,
        "sudo_password": SUDO_PASSWORD
    }
    print(f"✓ Payload created for {USERNAME}@{HOST}:{PORT}")
    
    # Step 3: Send to backend (plain JSON for testing)
    print(f"\n3. Sending request to {BACKEND_URL}/api/v1/connect")
    try:
        response = requests.post(
            f"{BACKEND_URL}/api/v1/connect",
            json=payload,
            timeout=30
        )
        
        print(f"✓ Response received (HTTP {response.status_code})")
        
        # Step 4: Check result
        result = response.json()
        print(f"\n4. Result:")
        print(f"   Connected: {result.get('connected')}")
        print(f"   User: {result.get('user')}")
        print(f"   Host: {result.get('host')}")
        
        if result.get('error'):
            print(f"   Error: {result.get('error')}")
        
        if result.get('connected'):
            print(f"\n{'=' * 60}")
            print(f"✓ SUCCESS! Authentication with private key works!")
            print(f"{'=' * 60}")
            return True
        else:
            print(f"\n{'=' * 60}")
            print(f"✗ FAILED: {result.get('error')}")
            print(f"{'=' * 60}")
            return False
            
    except requests.exceptions.ConnectionError:
        print(f"✗ Error: Could not connect to backend at {BACKEND_URL}")
        print(f"   Make sure the backend is running:")
        print(f"   cd server_setup_agent && docker compose up -d")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def test_encrypted_payload():
    """Test with encrypted payload"""
    print("\n\n" + "=" * 60)
    print("Testing Encrypted Private Key Authentication")
    print("=" * 60)
    
    try:
        from app.utils.encryption import encrypt_payload
        import requests
        import os
        
        # Configuration
        BACKEND_URL = os.getenv('BACKEND_URL', 'http://localhost:8000')
        PRIVATE_KEY_PATH = os.path.expanduser('~/.ssh/id_ed25519')
        ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY', 'my-super-secret-encryption-key')
        HOST = '31.97.224.45'
        PORT = 2200
        USERNAME = 'bipin'
        SUDO_PASSWORD = 'MyPassword123'  # Change this
        
        # Read private key
        print(f"\n1. Reading private key from: {PRIVATE_KEY_PATH}")
        with open(PRIVATE_KEY_PATH, 'r') as f:
            private_key = f.read()
        print(f"✓ Private key loaded")
        
        # Create and encrypt payload
        print(f"\n2. Encrypting payload")
        payload = {
            "host": HOST,
            "port": PORT,
            "username": USERNAME,
            "private_key": private_key,
            "sudo_password": SUDO_PASSWORD
        }
        
        encrypted_data = encrypt_payload(payload, ENCRYPTION_KEY)
        print(f"✓ Payload encrypted ({len(encrypted_data)} chars)")
        
        # Send to backend
        print(f"\n3. Sending encrypted request to {BACKEND_URL}/api/v1/connect")
        response = requests.post(
            f"{BACKEND_URL}/api/v1/connect",
            json={"encrypted_data": encrypted_data},
            timeout=30
        )
        
        print(f"✓ Response received (HTTP {response.status_code})")
        
        # Check result
        result = response.json()
        print(f"\n4. Result:")
        print(f"   Connected: {result.get('connected')}")
        print(f"   User: {result.get('user')}")
        print(f"   Host: {result.get('host')}")
        
        if result.get('error'):
            print(f"   Error: {result.get('error')}")
        
        if result.get('connected'):
            print(f"\n{'=' * 60}")
            print(f"✓ SUCCESS! Encrypted authentication works!")
            print(f"{'=' * 60}")
            return True
        else:
            print(f"\n{'=' * 60}")
            print(f"✗ FAILED: {result.get('error')}")
            print(f"{'=' * 60}")
            return False
            
    except ImportError:
        print("✗ Could not import encryption module")
        print("  Make sure you're running from the server_setup_agent directory")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


if __name__ == "__main__":
    print("\n")
    print("╔════════════════════════════════════════════════════════╗")
    print("║   Private Key Authentication Test Suite               ║")
    print("╚════════════════════════════════════════════════════════╝")
    
    # Test 1: Plain JSON
    success1 = test_private_key_auth()
    
    # Test 2: Encrypted payload (optional)
    print("\n\nWould you like to test encrypted authentication? (y/n): ", end='')
    try:
        answer = input().lower().strip()
        if answer == 'y' or answer == 'yes':
            success2 = test_encrypted_payload()
        else:
            success2 = None
    except:
        success2 = None
    
    # Summary
    print("\n\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    print(f"Plain JSON Authentication:      {'✓ PASS' if success1 else '✗ FAIL'}")
    if success2 is not None:
        print(f"Encrypted Authentication:       {'✓ PASS' if success2 else '✗ FAIL'}")
    print("=" * 60)
    
    sys.exit(0 if success1 else 1)
