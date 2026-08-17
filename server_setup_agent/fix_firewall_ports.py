#!/usr/bin/env python3
"""
Quick script to add missing ports 80 and 443 to UFW on an existing server.
Use this when nginx or other web services need to be accessible.
"""

import subprocess
import sys

def run_cmd(cmd):
    """Execute a shell command and return output."""
    print(f"→ Running: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.stdout:
        print(f"  {result.stdout.strip()}")
    if result.stderr:
        print(f"  ERROR: {result.stderr.strip()}", file=sys.stderr)
    return result.returncode == 0

def main():
    print("=" * 60)
    print("UFW Firewall Port Configuration")
    print("=" * 60)
    
    ports_to_add = [
        ("80", "tcp"),
        ("443", "tcp"),
    ]
    
    # Check current status
    print("\n📊 Current UFW Status:")
    run_cmd("sudo ufw status verbose")
    
    print("\n🔓 Adding HTTP/HTTPS ports:")
    all_ok = True
    for port, protocol in ports_to_add:
        success = run_cmd(f"sudo ufw allow {port}/{protocol}")
        if not success:
            all_ok = False
            print(f"  ❌ Failed to allow port {port}/{protocol}")
        else:
            print(f"  ✓ Port {port}/{protocol} added")
    
    print("\n📊 New UFW Status:")
    run_cmd("sudo ufw status verbose")
    
    if all_ok:
        print("\n✅ All ports configured successfully!")
        return 0
    else:
        print("\n❌ Some ports failed to configure. Check sudo access.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
