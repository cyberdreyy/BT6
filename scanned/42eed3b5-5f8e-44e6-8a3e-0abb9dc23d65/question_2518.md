# Q2518: classification used for both listing and authorization in utils.ts

## Question
The same predicate powers wallet listings and delegation eligibility; can an attacker exploit that dual use through getAllUserEmbeddedWallets (eth then solana) so a wallet visible in a list is wrongly assumed delegable?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Compare the listing set with the delegable set for a mixed user.
- Invariant to test: Listing and authorization predicates must be separately justified.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert getAllUserEmbeddedWallets (eth then solana) re-checks eligibility independently of listing.
