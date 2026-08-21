# Q0098: root wallet selected positionally in utils.ts

## Question
getRootWallet returns the first ethereum embedded wallet, falling back to the first solana one, unless the account is marked imported; can an unprivileged attacker influence account ordering so getAllUserEmbeddedWallets (eth then solana) delegates under a root wallet the user never chose?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Construct a user with several embedded wallets and observe which becomes the root in the consent payload.
- Invariant to test: The root wallet used for delegation must be explicitly selected and confirmed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build a multi-wallet user and assert getAllUserEmbeddedWallets (eth then solana) requires an explicit root.
