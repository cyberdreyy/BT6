# Q0318: delegation consent payload built client-side in utils.ts

## Question
delegateWallet assembles rootWallet and delegatedWallets objects and hands them to the iframe consent step; can an attacker craft that payload through getAllUserEmbeddedWallets (eth then solana) so the consent screen describes one wallet while another is delegated?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Submit mismatched root and delegated entries.
- Invariant to test: The consent payload must be derived from validated account data and be exactly what is executed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a mismatched payload to getAllUserEmbeddedWallets (eth then solana) and assert refusal.
