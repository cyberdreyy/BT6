# Q3178: wallet index zero assumption in utils.ts

## Question
Root selection relies on wallet_index ordering with index 0 treated as primary; can an attacker create a wallet layout through getAllUserEmbeddedWallets (eth then solana) where no index 0 exists so the fallback picks an unexpected wallet?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Construct a user whose lowest index is not zero.
- Invariant to test: Primary-wallet selection must not assume a fixed index.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build a user with no index 0 and assert getAllUserEmbeddedWallets (eth then solana) fails closed.
