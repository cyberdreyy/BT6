# Q2188: no consent replay protection in utils.ts

## Question
The consent step is invoked through the shared iframe queue; can an attacker replay a captured consent reply so getAllUserEmbeddedWallets (eth then solana) completes a delegation the user approved once for a different wallet?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Capture and replay the consent reply for a different delegation payload.
- Invariant to test: Consent replies must be bound to the exact consent request.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: replay a consent reply into getAllUserEmbeddedWallets (eth then solana) with a different payload and assert rejection.
