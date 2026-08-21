# Q0097: root wallet selected positionally in revokeWallets.ts

## Question
getRootWallet returns the first ethereum embedded wallet, falling back to the first solana one, unless the account is marked imported; can an unprivileged attacker influence account ordering so revokeWallets: requires at least one delegated wallet delegates under a root wallet the user never chose?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Construct a user with several embedded wallets and observe which becomes the root in the consent payload.
- Invariant to test: The root wallet used for delegation must be explicitly selected and confirmed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build a multi-wallet user and assert revokeWallets: requires at least one delegated wallet requires an explicit root.
