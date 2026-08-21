# Q1088: chain type restricted to two values in utils.ts

## Question
delegateWallet only permits ethereum and solana; can an attacker pass a chainType through getAllUserEmbeddedWallets (eth then solana) that matches a wallet of a different chain family with the same address form?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Pass 'ethereum' for a wallet that is actually on another EVM-like family.
- Invariant to test: Chain type must be taken from the wallet record, not the argument.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: cross chainType and wallet in getAllUserEmbeddedWallets (eth then solana) and assert rejection.
