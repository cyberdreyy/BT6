# Q3866: uppercase or checksummed address mismatch in maybeCreateWalletOnLogin.ts

## Question
Can an attacker exploit address case handling in maybeCreateWalletOnLogin so the address used for the nonce request differs textually from the address embedded in the signed message?

## Target
- File/function: [src/client/auth/maybeCreateWalletOnLogin.ts](src/client/auth/maybeCreateWalletOnLogin.ts) - maybeCreateWalletOnLogin, shouldCreateEmbeddedEthWallet, shouldCreateEmbeddedSolWallet, mergeUser
- Entrypoint: every auth.*.login* path after token storage
- Attacker controls: opts.embedded.{ethereum,solana}.createOnLogin, pre-existing linked wallets
- Exploit idea: Request the nonce with a lowercase address and sign a checksummed variant.
- Invariant to test: Address comparison in src/client/auth/maybeCreateWalletOnLogin.ts must be canonical.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: feed mixed-case address pairs to maybeCreateWalletOnLogin and assert consistent canonicalisation.
