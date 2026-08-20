"""
Backend Encryption Helper for Decrypting Frontend Payloads
============================================================

This module provides functions to decrypt AES-GCM encrypted payloads
sent from the React frontend.

Installation:
    pip install cryptography

Usage:
    from app.utils.encryption import decrypt_payload
    
    # In your FastAPI route:
    decrypted_data = decrypt_payload(request.encrypted_data, ENCRYPTION_KEY)
"""
import base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import json
from typing import Dict, Any


def decrypt_payload(encrypted_data: str, encryption_key: str) -> Dict[str, Any]:
    """
    Decrypt an AES-GCM encrypted payload from the frontend.
    
    Args:
        encrypted_data: Base64-encoded string containing IV + encrypted data
        encryption_key: Secret key used for encryption (must match frontend key)
    
    Returns:
        Decrypted payload as a dictionary
    
    Raises:
        ValueError: If decryption fails or data is invalid
    """
    try:
        # Decode base64
        combined = base64.b64decode(encrypted_data)
        
        # Extract IV (first 12 bytes) and encrypted data (remaining bytes)
        iv = combined[:12]
        encrypted = combined[12:]
        
        # Prepare key - pad to 32 bytes (256 bits) to match frontend
        key_bytes = encryption_key.encode('utf-8')
        key_bytes = key_bytes.ljust(32, b'0')[:32]
        
        # Create AES-GCM cipher and decrypt
        aesgcm = AESGCM(key_bytes)
        decrypted_bytes = aesgcm.decrypt(iv, encrypted, None)
        
        # Decode UTF-8 and parse JSON
        decrypted_str = decrypted_bytes.decode('utf-8')
        return json.loads(decrypted_str)
    
    except Exception as e:
        raise ValueError(f"Decryption failed: {str(e)}")


def encrypt_payload(data: Dict[str, Any], encryption_key: str) -> str:
    """
    Encrypt a payload using AES-GCM (for backend-to-frontend communication).
    
    Args:
        data: Dictionary to encrypt
        encryption_key: Secret key used for encryption
    
    Returns:
        Base64-encoded string containing IV + encrypted data
    
    Raises:
        ValueError: If encryption fails
    """
    try:
        import os
        
        # Prepare key - pad to 32 bytes (256 bits)
        key_bytes = encryption_key.encode('utf-8')
        key_bytes = key_bytes.ljust(32, b'0')[:32]
        
        # Create AES-GCM cipher
        aesgcm = AESGCM(key_bytes)
        
        # Generate random IV (12 bytes for GCM)
        iv = os.urandom(12)
        
        # Convert data to JSON and encode to bytes
        json_str = json.dumps(data)
        plaintext = json_str.encode('utf-8')
        
        # Encrypt
        encrypted = aesgcm.encrypt(iv, plaintext, None)
        
        # Combine IV + encrypted data and encode as base64
        combined = iv + encrypted
        return base64.b64encode(combined).decode('utf-8')
    
    except Exception as e:
        raise ValueError(f"Encryption failed: {str(e)}")
