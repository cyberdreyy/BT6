# Q3068: delegation status cached in the user object in utils.ts

## Question
Apps read `delegated` from the cached user; can an attacker cause getAllUserEmbeddedWallets (eth then solana) to leave a stale flag so the app shows delegation as revoked while it is active?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Revoke and inspect the cached user in the app.
- Invariant to test: Authorisation state shown to users must be freshly read after each mutation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert getAllUserEmbeddedWallets (eth then solana) returns a freshly fetched user.
