# Q1336: 30 day refresh cookie on a shared browser in maybeCreateWalletOnLogin.ts

## Question
Session.storeRefreshTokenForUser sets a 30-day refresh cookie; after maybeCreateWalletOnLogin, can a later unprivileged user of the same browser profile resume the previous account because logout only clears the active user's keys?

## Target
- File/function: [src/client/auth/maybeCreateWalletOnLogin.ts](src/client/auth/maybeCreateWalletOnLogin.ts) - maybeCreateWalletOnLogin, shouldCreateEmbeddedEthWallet, shouldCreateEmbeddedSolWallet, mergeUser
- Entrypoint: every auth.*.login* path after token storage
- Attacker controls: opts.embedded.{ethereum,solana}.createOnLogin, pre-existing linked wallets
- Exploit idea: Log in, close the tab without logout, then in a new SDK instance call refreshSession and observe a restored session.
- Invariant to test: A session must not be resumable after the SDK's own clear paths have run for that user.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: run maybeCreateWalletOnLogin, call destroyLocalState, construct a fresh Privy client and assert refreshSession throws MISSING_OR_INVALID_TOKEN.
