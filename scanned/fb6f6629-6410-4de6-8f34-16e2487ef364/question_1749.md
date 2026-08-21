# Q1749: delegation state confirmed by refresh only in DelegatedWalletsApi.ts

## Question
Both flows end by re-reading the user; can an attacker return a refresh that misreports delegation so DelegatedWalletsApi.revoke (WalletsRevoke reports success for an operation that failed?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Return a refresh with the delegated flag flipped.
- Invariant to test: Reported success must be derived from the operation result, not a subsequent read.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return a contradicting refresh and assert DelegatedWalletsApi.revoke (WalletsRevoke reports failure.
