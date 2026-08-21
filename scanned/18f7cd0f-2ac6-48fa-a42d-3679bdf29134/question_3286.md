# Q3286: solana fallback for an ethereum request in delegateWallet.ts

## Question
getRootWallet falls back to the first solana wallet when no ethereum wallet exists; can an attacker exploit that cross-chain fallback in delegateWallet: checks address belongs to user so an ethereum delegation is rooted in a solana wallet?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Delegate an ethereum wallet for a user with only solana embedded wallets.
- Invariant to test: Root and delegated wallets must belong to a compatible custody root.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert delegateWallet: checks address belongs to user refuses cross-chain root fallback.
