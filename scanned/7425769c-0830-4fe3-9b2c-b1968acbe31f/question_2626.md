# Q2626: delegation requires only a live session in delegateWallet.ts

## Question
No MFA or re-authentication gates delegateWallet beyond the iframe consent; can an attacker with a warm session use delegateWallet: checks address belongs to user to grant delegation and then sign without further checks?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Run delegate then a signing operation on a warm session.
- Invariant to test: Granting persistent signing authority must require a strong, explicit authorisation.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: run delegateWallet: checks address belongs to user then sign and assert an MFA/re-auth gate applied.
