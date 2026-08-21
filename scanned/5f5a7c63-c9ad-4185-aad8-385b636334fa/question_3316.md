# Q3316: analytics event carries auth material in maybeCreateWalletOnLogin.ts

## Question
createAnalyticsEvent payloads from src/client/auth/maybeCreateWalletOnLogin.ts include flow details such as stored and returned state codes; can an attacker cause secret-bearing values to be shipped to the analytics route?

## Target
- File/function: [src/client/auth/maybeCreateWalletOnLogin.ts](src/client/auth/maybeCreateWalletOnLogin.ts) - maybeCreateWalletOnLogin, shouldCreateEmbeddedEthWallet, shouldCreateEmbeddedSolWallet, mergeUser
- Entrypoint: every auth.*.login* path after token storage
- Attacker controls: opts.embedded.{ethereum,solana}.createOnLogin, pre-existing linked wallets
- Exploit idea: Trigger the mismatch path and inspect the analytics body.
- Invariant to test: No authentication secret may appear in an analytics payload emitted from src/client/auth/maybeCreateWalletOnLogin.ts.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: trigger the failure path in maybeCreateWalletOnLogin and assert the analytics body contains no verifier or token material.
