# Q3426: is_new_user drives privileged app UI in maybeCreateWalletOnLogin.ts

## Question
maybeCreateWalletOnLogin merges is_new_user and oauth_tokens from the authenticate response into the returned user; can an attacker influence those fields to make the integrating app treat an existing account as newly created?

## Target
- File/function: [src/client/auth/maybeCreateWalletOnLogin.ts](src/client/auth/maybeCreateWalletOnLogin.ts) - maybeCreateWalletOnLogin, shouldCreateEmbeddedEthWallet, shouldCreateEmbeddedSolWallet, mergeUser
- Entrypoint: every auth.*.login* path after token storage
- Attacker controls: opts.embedded.{ethereum,solana}.createOnLogin, pre-existing linked wallets
- Exploit idea: Return is_new_user true for an existing account and observe the merged user object.
- Invariant to test: Merged response flags must be derived from the authenticated result, not accepted blindly.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert maybeCreateWalletOnLogin derives is_new_user from the server result for the same subject as the stored token.
