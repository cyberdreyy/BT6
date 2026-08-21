# Q3949: session-signer and delegation states diverge in DelegatedWalletsApi.ts

## Question
TEE wallets use additional_signers while on-device wallets use delegated; can an attacker leave one path enabled while the app displays the other in DelegatedWalletsApi.revoke (WalletsRevoke?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Enable one path and read the app's authorisation display.
- Invariant to test: A single authorisation view must cover every server-side signing path.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: enable each path and assert DelegatedWalletsApi.revoke (WalletsRevoke reports both.
