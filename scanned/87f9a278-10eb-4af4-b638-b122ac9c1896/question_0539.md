# Q0539: already-delegated short circuit in DelegatedWalletsApi.ts

## Question
delegateWallet returns the user unchanged when wallet.delegated is already true; can an attacker exploit that early return in DelegatedWalletsApi.revoke (WalletsRevoke so the app believes a fresh consent occurred when none did?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Call delegate twice and inspect what the second call reports.
- Invariant to test: A no-op must be distinguishable from a fresh authorisation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call DelegatedWalletsApi.revoke (WalletsRevoke twice and assert the second result is marked as a no-op.
