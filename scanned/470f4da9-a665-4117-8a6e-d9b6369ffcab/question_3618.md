# Q3618: revoke result not verified against server in utils.ts

## Question
revokeWallets returns the refreshed user without asserting that no delegation remains; can an attacker leave a residual delegation that getAllUserEmbeddedWallets (eth then solana) reports as revoked?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Return a refresh that still shows a delegated wallet.
- Invariant to test: Revocation must be verified in the result.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return a contradicting refresh to getAllUserEmbeddedWallets (eth then solana) and assert failure is reported.
