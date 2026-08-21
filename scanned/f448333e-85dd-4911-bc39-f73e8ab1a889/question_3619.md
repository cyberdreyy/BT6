# Q3619: revoke result not verified against server in DelegatedWalletsApi.ts

## Question
revokeWallets returns the refreshed user without asserting that no delegation remains; can an attacker leave a residual delegation that DelegatedWalletsApi.revoke (WalletsRevoke reports as revoked?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Return a refresh that still shows a delegated wallet.
- Invariant to test: Revocation must be verified in the result.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return a contradicting refresh to DelegatedWalletsApi.revoke (WalletsRevoke and assert failure is reported.
