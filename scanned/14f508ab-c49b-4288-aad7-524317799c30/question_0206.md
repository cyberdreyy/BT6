# Q0206: imported flag flips the root in delegateWallet.ts

## Question
getRootWallet returns the account itself when imported is true; can an attacker present an account object with imported set through privy.delegated.delegateWallet({address, chainType}) so delegateWallet: checks address belongs to user treats an arbitrary wallet as its own root?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Pass a crafted account with imported true.
- Invariant to test: Account flags used for delegation must come from server-confirmed state.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass {imported:true} on a crafted account to delegateWallet: checks address belongs to user and assert re-validation.
