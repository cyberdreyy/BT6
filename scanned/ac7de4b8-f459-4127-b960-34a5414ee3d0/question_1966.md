# Q1966: delegated wallets carry a wallet index in delegateWallet.ts

## Question
The delegation payload includes walletIndex from the account object; can an attacker submit an index through delegateWallet: checks address belongs to user that points at a different wallet than the address?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Submit an address and index that disagree.
- Invariant to test: Address and index in the delegation payload must be verified consistent.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a disagreeing pair to delegateWallet: checks address belongs to user and assert rejection.
