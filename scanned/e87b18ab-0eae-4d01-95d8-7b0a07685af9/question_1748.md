# Q1748: delegation state confirmed by refresh only in utils.ts

## Question
Both flows end by re-reading the user; can an attacker return a refresh that misreports delegation so getAllUserEmbeddedWallets (eth then solana) reports success for an operation that failed?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Return a refresh with the delegated flag flipped.
- Invariant to test: Reported success must be derived from the operation result, not a subsequent read.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return a contradicting refresh and assert getAllUserEmbeddedWallets (eth then solana) reports failure.
