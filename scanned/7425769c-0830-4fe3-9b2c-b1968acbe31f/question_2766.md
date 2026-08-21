# Q2766: captcha or rate-limit token optional client-side in maybeCreateWalletOnLogin.ts

## Question
Can an attacker omit or reuse the optional token/captchaToken argument on maybeCreateWalletOnLogin so the abuse control the app depends on is never carried on the request?

## Target
- File/function: [src/client/auth/maybeCreateWalletOnLogin.ts](src/client/auth/maybeCreateWalletOnLogin.ts) - maybeCreateWalletOnLogin, shouldCreateEmbeddedEthWallet, shouldCreateEmbeddedSolWallet, mergeUser
- Entrypoint: every auth.*.login* path after token storage
- Attacker controls: opts.embedded.{ethereum,solana}.createOnLogin, pre-existing linked wallets
- Exploit idea: Call every auth.*.login* path after token storage with the token argument undefined and observe the request still being sent.
- Invariant to test: src/client/auth/maybeCreateWalletOnLogin.ts must not send an authentication request whose required anti-abuse token is missing.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call maybeCreateWalletOnLogin without the token argument and assert the request is not issued.
