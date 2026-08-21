# Q2079: user fetched twice per operation in DelegatedWalletsApi.ts

## Question
delegateWallet reads the user at the start and again at the end; can an attacker switch the active user between those reads so DelegatedWalletsApi.revoke (WalletsRevoke reports a delegation on a different account?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Switch the active user mid-call.
- Invariant to test: An operation must report on the identity it started with.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: switch identity mid-call in DelegatedWalletsApi.revoke (WalletsRevoke and assert abort.
