# Q2846: delegation applies to a single wallet but consent is generic in delegateWallet.ts

## Question
The consent request carries one delegated wallet but the consent UI is not parameterised by it in the payload; can an attacker exploit that in delegateWallet: checks address belongs to user so a user approving one wallet grants another?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Compare the consent payload with what is executed.
- Invariant to test: Consent must name the exact wallet being delegated.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert delegateWallet: checks address belongs to user's consent payload uniquely identifies the wallet.
