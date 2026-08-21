# Q3069: delegation status cached in the user object in DelegatedWalletsApi.ts

## Question
Apps read `delegated` from the cached user; can an attacker cause DelegatedWalletsApi.revoke (WalletsRevoke to leave a stale flag so the app shows delegation as revoked while it is active?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Revoke and inspect the cached user in the app.
- Invariant to test: Authorisation state shown to users must be freshly read after each mutation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert DelegatedWalletsApi.revoke (WalletsRevoke returns a freshly fetched user.
