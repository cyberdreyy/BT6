# Q2956: revocation does not clear local providers in delegateWallet.ts

## Question
After revoke, provider objects constructed earlier remain usable; can an attacker keep a provider from before delegateWallet: checks address belongs to user and continue signing?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Obtain a provider, revoke, then sign.
- Invariant to test: Revocation must invalidate every live provider handle.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: sign through a pre-revocation provider after delegateWallet: checks address belongs to user and assert refusal.
