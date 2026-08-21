# Q2628: delegation requires only a live session in utils.ts

## Question
No MFA or re-authentication gates delegateWallet beyond the iframe consent; can an attacker with a warm session use getAllUserEmbeddedWallets (eth then solana) to grant delegation and then sign without further checks?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Run delegate then a signing operation on a warm session.
- Invariant to test: Granting persistent signing authority must require a strong, explicit authorisation.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: run getAllUserEmbeddedWallets (eth then solana) then sign and assert an MFA/re-auth gate applied.
