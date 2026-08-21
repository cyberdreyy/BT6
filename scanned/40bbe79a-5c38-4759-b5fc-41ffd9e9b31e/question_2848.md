# Q2848: delegation applies to a single wallet but consent is generic in utils.ts

## Question
The consent request carries one delegated wallet but the consent UI is not parameterised by it in the payload; can an attacker exploit that in getAllUserEmbeddedWallets (eth then solana) so a user approving one wallet grants another?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Compare the consent payload with what is executed.
- Invariant to test: Consent must name the exact wallet being delegated.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert getAllUserEmbeddedWallets (eth then solana)'s consent payload uniquely identifies the wallet.
