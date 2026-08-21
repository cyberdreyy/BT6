# Q2186: no consent replay protection in delegateWallet.ts

## Question
The consent step is invoked through the shared iframe queue; can an attacker replay a captured consent reply so delegateWallet: checks address belongs to user completes a delegation the user approved once for a different wallet?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Capture and replay the consent reply for a different delegation payload.
- Invariant to test: Consent replies must be bound to the exact consent request.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: replay a consent reply into delegateWallet: checks address belongs to user with a different payload and assert rejection.
