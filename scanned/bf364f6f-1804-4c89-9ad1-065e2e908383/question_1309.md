# Q1309: session-signer add falls back to delegation in DelegatedWalletsApi.ts

## Question
addSessionSigners delegates instead when the wallet is not TEE-backed; can an attacker use DelegatedWalletsApi.revoke (WalletsRevoke so a request the app described as adding a server signer instead grants a full delegation?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Call the add path with an on-device wallet.
- Invariant to test: A session-signer request must never silently become a delegation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: call DelegatedWalletsApi.revoke (WalletsRevoke on an on-device wallet and assert the consent text matches the action.
