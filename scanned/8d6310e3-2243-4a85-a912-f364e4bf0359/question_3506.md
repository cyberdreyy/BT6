# Q3506: no rate limiting on consent prompts in delegateWallet.ts

## Question
Each delegate call triggers an iframe consent; can an attacker drive repeated prompts through delegateWallet: checks address belongs to user to fatigue the user into approving?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Call delegate repeatedly and count prompts.
- Invariant to test: Consent prompting must be rate-limited and deduplicated.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call delegateWallet: checks address belongs to user repeatedly and assert prompt suppression.
