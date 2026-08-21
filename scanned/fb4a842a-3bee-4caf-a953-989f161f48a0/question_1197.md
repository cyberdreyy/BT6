# Q1197: wallet list built by concatenation in revokeWallets.ts

## Question
getAllUserEmbeddedWallets concatenates ethereum then solana wallets; can an attacker exploit ordering assumptions in revokeWallets: requires at least one delegated wallet so an index-based selection picks the wrong wallet?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Construct users where the concatenation order changes which wallet is first.
- Invariant to test: Wallet selection must be by identity, not by position.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: permute account order and assert revokeWallets: requires at least one delegated wallet selects the same wallet.
