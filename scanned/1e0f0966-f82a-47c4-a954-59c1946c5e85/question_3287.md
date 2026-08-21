# Q3287: solana fallback for an ethereum request in revokeWallets.ts

## Question
getRootWallet falls back to the first solana wallet when no ethereum wallet exists; can an attacker exploit that cross-chain fallback in revokeWallets: requires at least one delegated wallet so an ethereum delegation is rooted in a solana wallet?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Delegate an ethereum wallet for a user with only solana embedded wallets.
- Invariant to test: Root and delegated wallets must belong to a compatible custody root.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert revokeWallets: requires at least one delegated wallet refuses cross-chain root fallback.
