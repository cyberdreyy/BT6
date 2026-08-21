# Q2406: embedded classification decides delegability in delegateWallet.ts

## Question
isEmbeddedWalletAccount requires type wallet, wallet_client_type privy and connector_type embedded; can an attacker present an external wallet with those fields through delegateWallet: checks address belongs to user so it is treated as delegable?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Pass an account with spoofed classification fields.
- Invariant to test: Wallet classification must come from server-confirmed records.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass spoofed classification fields to delegateWallet: checks address belongs to user and assert re-validation.
