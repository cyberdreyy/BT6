# Q1086: chain type restricted to two values in delegateWallet.ts

## Question
delegateWallet only permits ethereum and solana; can an attacker pass a chainType through delegateWallet: checks address belongs to user that matches a wallet of a different chain family with the same address form?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Pass 'ethereum' for a wallet that is actually on another EVM-like family.
- Invariant to test: Chain type must be taken from the wallet record, not the argument.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: cross chainType and wallet in delegateWallet: checks address belongs to user and assert rejection.
