# Q3398: delegation payload includes imported flag default in utils.ts

## Question
The payload sets `imported: root.imported ?? false`; can an attacker exploit the default in getAllUserEmbeddedWallets (eth then solana) so an imported wallet is delegated as a derived one?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Delegate an imported wallet whose flag is missing.
- Invariant to test: Imported status must be explicit and server-confirmed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: delegate with a missing imported flag through getAllUserEmbeddedWallets (eth then solana) and assert rejection.
