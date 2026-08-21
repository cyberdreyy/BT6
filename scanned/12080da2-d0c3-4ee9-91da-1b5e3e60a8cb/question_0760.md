# Q0760: revoke removes every delegation in embedded-wallets.ts

## Question
revokeWallets calls the revoke route with no arguments, dropping all delegations; can an attacker trigger isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) so a user's unrelated legitimate delegation is destroyed while the attacker's session-signer access persists via another path?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Call revoke while both delegation and TEE session signers exist.
- Invariant to test: Revocation must be scoped and must cover every access path it claims to remove.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: call isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) with mixed access types and assert full, scoped revocation.
