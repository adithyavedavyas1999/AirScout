#!/usr/bin/env python3
"""
Generate VAPID keys for Web Push notifications.

VAPID (Voluntary Application Server Identification) keys are required
for sending Web Push notifications.

Usage:
    python scripts/generate_vapid_keys.py

Output:
    VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY to add to:
    - GitHub Secrets (for the alert service)
    - PWA index.html (public key only)

Author: AirScout Team
"""

import base64
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization


def generate_vapid_keys():
    """Generate VAPID key pair for Web Push."""
    
    # Generate private key
    private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
    
    # Get public key
    public_key = private_key.public_key()
    
    # Serialize private key (for server)
    private_bytes = private_key.private_numbers().private_value.to_bytes(32, 'big')
    private_key_b64 = base64.urlsafe_b64encode(private_bytes).decode('utf-8').rstrip('=')
    
    # Serialize public key (for client)
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint
    )
    public_key_b64 = base64.urlsafe_b64encode(public_bytes).decode('utf-8').rstrip('=')
    
    return public_key_b64, private_key_b64


def main():
    print("=" * 60)
    print("VAPID Key Generator for AirScout Web Push")
    print("=" * 60)
    print()
    
    public_key, private_key = generate_vapid_keys()
    
    print("📋 Add these to your GitHub Secrets:")
    print("-" * 40)
    print(f"VAPID_PUBLIC_KEY={public_key}")
    print(f"VAPID_PRIVATE_KEY={private_key}")
    print()
    
    print("📱 Add the PUBLIC key to pwa/index.html:")
    print("-" * 40)
    print(f"applicationServerKey: '{public_key}'")
    print()
    
    print("⚠️  Keep the PRIVATE key secret!")
    print("    Only add it to GitHub Secrets, never commit it.")
    print()


if __name__ == "__main__":
    main()

