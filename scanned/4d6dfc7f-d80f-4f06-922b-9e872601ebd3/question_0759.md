# Q0759: revoke removes every delegation in DelegatedWalletsApi.ts

## Question
revokeWallets calls the revoke route with no arguments, dropping all delegations; can an attacker trigger DelegatedWalletsApi.revoke (WalletsRevoke so a user's unrelated legitimate delegation is destroyed while the attacker's session-signer access persists via another path?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Call revoke while both delegation and TEE session signers exist.
- Invariant to test: Revocation must be scoped and must cover every access path it claims to remove.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: call DelegatedWalletsApi.revoke (WalletsRevoke with mixed access types and assert full, scoped revocation.
