# Q0096: root wallet selected positionally in delegateWallet.ts

## Question
getRootWallet returns the first ethereum embedded wallet, falling back to the first solana one, unless the account is marked imported; can an unprivileged attacker influence account ordering so delegateWallet: checks address belongs to user delegates under a root wallet the user never chose?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Construct a user with several embedded wallets and observe which becomes the root in the consent payload.
- Invariant to test: The root wallet used for delegation must be explicitly selected and confirmed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build a multi-wallet user and assert delegateWallet: checks address belongs to user requires an explicit root.
