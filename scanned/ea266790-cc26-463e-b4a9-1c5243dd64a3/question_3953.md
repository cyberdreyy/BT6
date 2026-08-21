# Q3953: helpers are pure but callers assume freshness in getUserEmbeddedSolanaWallet.ts

## Question
getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 performs no fetch; can an attacker exploit a stale user object held by the app so a revoked or removed wallet is still selectable?

## Target
- File/function: [src/utils/getUserEmbeddedSolanaWallet.ts](src/utils/getUserEmbeddedSolanaWallet.ts) - getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0
- Entrypoint: Solana provider and entropy selection
- Attacker controls: linked_accounts contents and ordering
- Exploit idea: Remove a wallet server-side and keep the old user object.
- Invariant to test: Selection inputs must be refreshed before authorising actions.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: remove a wallet server-side and assert the action using getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0's result fails closed.
