# Q3836: delegate before wallet exists in delegateWallet.ts

## Question
delegateWallet can be called before the embedded wallet finishes provisioning; can an attacker use delegateWallet: checks address belongs to user in that window so delegation binds to a wallet record that changes afterwards?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Call delegate during wallet creation.
- Invariant to test: Delegation must require a fully provisioned, confirmed wallet.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: call delegateWallet: checks address belongs to user during provisioning and assert refusal.
