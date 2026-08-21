# Q3536: oauth_tokens emitted to any listener in maybeCreateWalletOnLogin.ts

## Question
Provider tokens from maybeCreateWalletOnLogin are emitted through the session 'oauth_tokens_granted' event to every registered listener; can an attacker register or keep a listener that receives another flow's provider tokens?

## Target
- File/function: [src/client/auth/maybeCreateWalletOnLogin.ts](src/client/auth/maybeCreateWalletOnLogin.ts) - maybeCreateWalletOnLogin, shouldCreateEmbeddedEthWallet, shouldCreateEmbeddedSolWallet, mergeUser
- Entrypoint: every auth.*.login* path after token storage
- Attacker controls: opts.embedded.{ethereum,solana}.createOnLogin, pre-existing linked wallets
- Exploit idea: Attach a listener, trigger an unrelated login flow, and observe the tokens delivered.
- Invariant to test: Provider tokens must only reach the flow that requested them.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: attach a listener, run an unrelated maybeCreateWalletOnLogin flow and assert the listener is not invoked.
