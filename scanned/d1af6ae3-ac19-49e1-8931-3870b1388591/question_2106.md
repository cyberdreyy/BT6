# Q2106: nonce reuse across two logins in maybeCreateWalletOnLogin.ts

## Question
Can an attacker reuse a nonce previously issued by init()/fetchNonce for the same address to authenticate a second time from a different device or context?

## Target
- File/function: [src/client/auth/maybeCreateWalletOnLogin.ts](src/client/auth/maybeCreateWalletOnLogin.ts) - maybeCreateWalletOnLogin, shouldCreateEmbeddedEthWallet, shouldCreateEmbeddedSolWallet, mergeUser
- Entrypoint: every auth.*.login* path after token storage
- Attacker controls: opts.embedded.{ethereum,solana}.createOnLogin, pre-existing linked wallets
- Exploit idea: Capture the nonce, complete a login, then replay message and signature.
- Invariant to test: Each issued nonce must be single-use for maybeCreateWalletOnLogin.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: complete a login and then replay the same message/signature and assert the second attempt fails.
