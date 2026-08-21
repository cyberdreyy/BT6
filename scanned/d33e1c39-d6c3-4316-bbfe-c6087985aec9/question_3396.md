# Q3396: delegation payload includes imported flag default in delegateWallet.ts

## Question
The payload sets `imported: root.imported ?? false`; can an attacker exploit the default in delegateWallet: checks address belongs to user so an imported wallet is delegated as a derived one?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Delegate an imported wallet whose flag is missing.
- Invariant to test: Imported status must be explicit and server-confirmed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: delegate with a missing imported flag through delegateWallet: checks address belongs to user and assert rejection.
