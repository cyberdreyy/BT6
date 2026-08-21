# Q1196: wallet list built by concatenation in delegateWallet.ts

## Question
getAllUserEmbeddedWallets concatenates ethereum then solana wallets; can an attacker exploit ordering assumptions in delegateWallet: checks address belongs to user so an index-based selection picks the wrong wallet?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Construct users where the concatenation order changes which wallet is first.
- Invariant to test: Wallet selection must be by identity, not by position.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: permute account order and assert delegateWallet: checks address belongs to user selects the same wallet.
