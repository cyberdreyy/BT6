# Q3508: no rate limiting on consent prompts in utils.ts

## Question
Each delegate call triggers an iframe consent; can an attacker drive repeated prompts through getAllUserEmbeddedWallets (eth then solana) to fatigue the user into approving?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Call delegate repeatedly and count prompts.
- Invariant to test: Consent prompting must be rate-limited and deduplicated.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call getAllUserEmbeddedWallets (eth then solana) repeatedly and assert prompt suppression.
