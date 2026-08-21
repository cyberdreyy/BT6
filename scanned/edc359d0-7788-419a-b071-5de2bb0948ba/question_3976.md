# Q3976: no expiry in the signed statement in maybeCreateWalletOnLogin.ts

## Question
The statement built in src/client/auth/maybeCreateWalletOnLogin.ts carries Issued At but no expiration; can an attacker replay a signature captured months earlier through maybeCreateWalletOnLogin?

## Target
- File/function: [src/client/auth/maybeCreateWalletOnLogin.ts](src/client/auth/maybeCreateWalletOnLogin.ts) - maybeCreateWalletOnLogin, shouldCreateEmbeddedEthWallet, shouldCreateEmbeddedSolWallet, mergeUser
- Entrypoint: every auth.*.login* path after token storage
- Attacker controls: opts.embedded.{ethereum,solana}.createOnLogin, pre-existing linked wallets
- Exploit idea: Sign once, store the message and signature, replay after a long delay.
- Invariant to test: Authentication statements must carry an expiry the client enforces.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert maybeCreateWalletOnLogin rejects a message whose Issued At is older than a short window.
