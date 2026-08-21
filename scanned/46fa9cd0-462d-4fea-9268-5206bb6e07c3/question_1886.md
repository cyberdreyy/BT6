# Q1886: domain and uri are caller-controlled in maybeCreateWalletOnLogin.ts

## Question
maybeCreateWalletOnLogin builds the signing statement from a caller-supplied domain and uri; can an attacker present a message whose domain names a different application so a signature harvested elsewhere authenticates here?

## Target
- File/function: [src/client/auth/maybeCreateWalletOnLogin.ts](src/client/auth/maybeCreateWalletOnLogin.ts) - maybeCreateWalletOnLogin, shouldCreateEmbeddedEthWallet, shouldCreateEmbeddedSolWallet, mergeUser
- Entrypoint: every auth.*.login* path after token storage
- Attacker controls: opts.embedded.{ethereum,solana}.createOnLogin, pre-existing linked wallets
- Exploit idea: Build a message with the victim app's domain, obtain a signature in another context, and submit it.
- Invariant to test: The signed statement must be bound to the origin actually performing the authentication.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert maybeCreateWalletOnLogin rejects a domain that does not match the configured app origin.
