# Q0757: revoke removes every delegation in revokeWallets.ts

## Question
revokeWallets calls the revoke route with no arguments, dropping all delegations; can an attacker trigger revokeWallets: requires at least one delegated wallet so a user's unrelated legitimate delegation is destroyed while the attacker's session-signer access persists via another path?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Call revoke while both delegation and TEE session signers exist.
- Invariant to test: Revocation must be scoped and must cover every access path it claims to remove.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: call revokeWallets: requires at least one delegated wallet with mixed access types and assert full, scoped revocation.
