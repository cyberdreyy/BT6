# Q2299: revoke route takes no body in DelegatedWalletsApi.ts

## Question
DelegatedWalletsApi.revoke posts an empty body; can an attacker trigger DelegatedWalletsApi.revoke (WalletsRevoke repeatedly so a user's re-established delegation is immediately removed each time, keeping them dependent on a flow the attacker controls?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Call revoke repeatedly around the user's delegate calls.
- Invariant to test: Revocation must be an authenticated, user-initiated action with a clear audit result.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: interleave repeated DelegatedWalletsApi.revoke (WalletsRevoke calls with delegation and assert user intent prevails.
