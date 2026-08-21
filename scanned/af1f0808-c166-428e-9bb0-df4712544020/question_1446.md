# Q1446: state code shared across simultaneous flows in maybeCreateWalletOnLogin.ts

## Question
privy:state_code and privy:code_verifier are single global keys; can an attacker cause maybeCreateWalletOnLogin to consume a verifier written by a different, concurrently started flow?

## Target
- File/function: [src/client/auth/maybeCreateWalletOnLogin.ts](src/client/auth/maybeCreateWalletOnLogin.ts) - maybeCreateWalletOnLogin, shouldCreateEmbeddedEthWallet, shouldCreateEmbeddedSolWallet, mergeUser
- Entrypoint: every auth.*.login* path after token storage
- Attacker controls: opts.embedded.{ethereum,solana}.createOnLogin, pre-existing linked wallets
- Exploit idea: Start a login OAuth flow and a recovery OAuth flow, then complete the first with the second's stored verifier.
- Invariant to test: Each authorization flow must consume only the PKCE material it generated.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: start two flows against one Storage and assert the second generateURL does not overwrite the first flow's verifier before it completes.
