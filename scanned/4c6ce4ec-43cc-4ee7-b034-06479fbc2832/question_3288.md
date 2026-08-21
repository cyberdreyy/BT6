# Q3288: solana fallback for an ethereum request in utils.ts

## Question
getRootWallet falls back to the first solana wallet when no ethereum wallet exists; can an attacker exploit that cross-chain fallback in getAllUserEmbeddedWallets (eth then solana) so an ethereum delegation is rooted in a solana wallet?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Delegate an ethereum wallet for a user with only solana embedded wallets.
- Invariant to test: Root and delegated wallets must belong to a compatible custody root.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert getAllUserEmbeddedWallets (eth then solana) refuses cross-chain root fallback.
