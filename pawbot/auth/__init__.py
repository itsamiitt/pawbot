"""Cryptographic device authentication and pairing.

Phase 19: Ed25519 keypair-based device authentication comprising:
  - KeypairManager    (Ed25519 key generation & signing)
  - DeviceRegistry    (device pairing, approval, revocation)
  - TokenService      (scoped JWT token issuance & verification)
  - ROLES / SCOPES    (role definitions)
"""

from pawbot.auth.keypair import KeypairManager
from pawbot.auth.device import DeviceRegistry
from pawbot.auth.tokens import TokenService
from pawbot.auth.roles import ROLES, SCOPES

__all__ = [
    "KeypairManager",
    "DeviceRegistry",
    "TokenService",
    "ROLES",
    "SCOPES",
]
