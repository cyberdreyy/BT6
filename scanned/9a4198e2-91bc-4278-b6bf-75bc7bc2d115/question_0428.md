# Q0428: ownership check by address equality in utils.ts

## Question
delegateWallet finds the target with `chain_type === n && address === t`; can an attacker submit a checksummed or padded address through getAllUserEmbeddedWallets (eth then solana) that fails or passes this check incorrectly?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Pass mixed-case and padded variants of an owned address.
- Invariant to test: Ownership comparison must be canonical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: table-test address forms through getAllUserEmbeddedWallets (eth then solana).
