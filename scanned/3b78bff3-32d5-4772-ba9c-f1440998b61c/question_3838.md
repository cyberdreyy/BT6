# Q3838: delegate before wallet exists in utils.ts

## Question
delegateWallet can be called before the embedded wallet finishes provisioning; can an attacker use getAllUserEmbeddedWallets (eth then solana) in that window so delegation binds to a wallet record that changes afterwards?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Call delegate during wallet creation.
- Invariant to test: Delegation must require a fully provisioned, confirmed wallet.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: call getAllUserEmbeddedWallets (eth then solana) during provisioning and assert refusal.
