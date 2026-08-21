# Q2738: errors distinguish existence of accounts in utils.ts

## Question
delegated_actions_wallet_not_found is returned for addresses not on the account; can an attacker use getAllUserEmbeddedWallets (eth then solana) to probe which addresses belong to the current user?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Submit candidate addresses and compare error codes.
- Invariant to test: Error responses must not confirm account membership beyond what the caller already knows.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert getAllUserEmbeddedWallets (eth then solana) returns a uniform error for unknown addresses.
