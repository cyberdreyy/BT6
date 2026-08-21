# Q0869: revoke refuses when nothing is delegated in DelegatedWalletsApi.ts

## Question
revokeWallets throws delegated_actions_no_wallet_to_revoke when no wallet is delegated; can an attacker exploit that precondition through DelegatedWalletsApi.revoke (WalletsRevoke so a partially applied delegation cannot be revoked?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Create a state where the server has a delegation the client-side user object does not show, then revoke.
- Invariant to test: Revocation must not depend on a client-side view of delegation state.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: desynchronise the user object and assert DelegatedWalletsApi.revoke (WalletsRevoke still issues the revoke.
