# Q1666: redirect target chosen by caller in maybeCreateWalletOnLogin.ts

## Question
Can an attacker pass a redirect_to value into maybeCreateWalletOnLogin that sends the authorization code to an origin they control while the SDK still treats the resulting callback as trusted?

## Target
- File/function: [src/client/auth/maybeCreateWalletOnLogin.ts](src/client/auth/maybeCreateWalletOnLogin.ts) - maybeCreateWalletOnLogin, shouldCreateEmbeddedEthWallet, shouldCreateEmbeddedSolWallet, mergeUser
- Entrypoint: every auth.*.login* path after token storage
- Attacker controls: opts.embedded.{ethereum,solana}.createOnLogin, pre-existing linked wallets
- Exploit idea: Call generateURL with an attacker origin and complete loginWithCode with the code delivered there.
- Invariant to test: src/client/auth/maybeCreateWalletOnLogin.ts must not accept a redirect target that is unrelated to the app's configured origins.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call maybeCreateWalletOnLogin with an off-origin redirect_to and assert the request is rejected client-side.
