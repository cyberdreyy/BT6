# Q3726: delegation errors surface wallet addresses in delegateWallet.ts

## Question
Error paths embed the address being delegated; can an attacker use delegateWallet: checks address belongs to user to extract another user's address from a shared error surface?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Trigger errors with candidate addresses and read the messages.
- Invariant to test: Errors must not echo identifiers the caller did not supply.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert delegateWallet: checks address belongs to user does not echo unrelated addresses.
