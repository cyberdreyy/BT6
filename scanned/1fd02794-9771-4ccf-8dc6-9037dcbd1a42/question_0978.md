# Q0978: TEE wallets rejected only client-side in utils.ts

## Question
delegateWallet and revokeWallets throw unsupported_wallet_type for unified (privy-v2) wallets based on the account object; can an attacker present an account through getAllUserEmbeddedWallets (eth then solana) that evades the check and reaches the delegation path?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Pass an account missing the id field or with a different recovery_method.
- Invariant to test: Custody-type checks must use server-confirmed account records.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass evasive account objects to getAllUserEmbeddedWallets (eth then solana) and assert re-validation.
