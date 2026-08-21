# Q1859: delegate then revoke race in DelegatedWalletsApi.ts

## Question
delegate and revoke both mutate the same server-side state with no client-side ordering; can an attacker interleave them through DelegatedWalletsApi.revoke (WalletsRevoke so the final state differs from the user's last intent?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Fire both concurrently and inspect the final state.
- Invariant to test: Concurrent authorisation mutations must be serialised or version-checked.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: race DelegatedWalletsApi.revoke (WalletsRevoke calls and assert the last intent wins deterministically.
