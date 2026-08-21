# Q2958: revocation does not clear local providers in utils.ts

## Question
After revoke, provider objects constructed earlier remain usable; can an attacker keep a provider from before getAllUserEmbeddedWallets (eth then solana) and continue signing?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Obtain a provider, revoke, then sign.
- Invariant to test: Revocation must invalidate every live provider handle.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: sign through a pre-revocation provider after getAllUserEmbeddedWallets (eth then solana) and assert refusal.
