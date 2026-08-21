# Q1198: wallet list built by concatenation in utils.ts

## Question
getAllUserEmbeddedWallets concatenates ethereum then solana wallets; can an attacker exploit ordering assumptions in getAllUserEmbeddedWallets (eth then solana) so an index-based selection picks the wrong wallet?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Construct users where the concatenation order changes which wallet is first.
- Invariant to test: Wallet selection must be by identity, not by position.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: permute account order and assert getAllUserEmbeddedWallets (eth then solana) selects the same wallet.
