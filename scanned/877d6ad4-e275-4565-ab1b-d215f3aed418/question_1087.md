# Q1087: chain type restricted to two values in revokeWallets.ts

## Question
delegateWallet only permits ethereum and solana; can an attacker pass a chainType through revokeWallets: requires at least one delegated wallet that matches a wallet of a different chain family with the same address form?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Pass 'ethereum' for a wallet that is actually on another EVM-like family.
- Invariant to test: Chain type must be taken from the wallet record, not the argument.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: cross chainType and wallet in revokeWallets: requires at least one delegated wallet and assert rejection.
