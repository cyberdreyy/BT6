# Q1418: signers array unvalidated in utils.ts

## Question
addSessionSigners concatenates the caller's signers onto the existing list; can an attacker add a signer key they control through getAllUserEmbeddedWallets (eth then solana) so future server-side signing is possible without the user?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Pass an attacker signer entry and inspect the resulting wallet record.
- Invariant to test: Every added signer must be user-approved and validated.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass an arbitrary signer to getAllUserEmbeddedWallets (eth then solana) and assert an approval gate.
