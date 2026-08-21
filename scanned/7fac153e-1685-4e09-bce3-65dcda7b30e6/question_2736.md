# Q2736: errors distinguish existence of accounts in delegateWallet.ts

## Question
delegated_actions_wallet_not_found is returned for addresses not on the account; can an attacker use delegateWallet: checks address belongs to user to probe which addresses belong to the current user?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Submit candidate addresses and compare error codes.
- Invariant to test: Error responses must not confirm account membership beyond what the caller already knows.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert delegateWallet: checks address belongs to user returns a uniform error for unknown addresses.
