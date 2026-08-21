# Q3176: wallet index zero assumption in delegateWallet.ts

## Question
Root selection relies on wallet_index ordering with index 0 treated as primary; can an attacker create a wallet layout through delegateWallet: checks address belongs to user where no index 0 exists so the fallback picks an unexpected wallet?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Construct a user whose lowest index is not zero.
- Invariant to test: Primary-wallet selection must not assume a fixed index.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build a user with no index 0 and assert delegateWallet: checks address belongs to user fails closed.
