# Q3728: delegation errors surface wallet addresses in utils.ts

## Question
Error paths embed the address being delegated; can an attacker use getAllUserEmbeddedWallets (eth then solana) to extract another user's address from a shared error surface?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Trigger errors with candidate addresses and read the messages.
- Invariant to test: Errors must not echo identifiers the caller did not supply.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert getAllUserEmbeddedWallets (eth then solana) does not echo unrelated addresses.
