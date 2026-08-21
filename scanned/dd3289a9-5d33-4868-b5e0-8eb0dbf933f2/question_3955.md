# Q3955: helpers are pure but callers assume freshness in getAllUserEmbeddedBitcoinWallets.ts

## Question
getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter performs no fetch; can an attacker exploit a stale user object held by the app so a revoked or removed wallet is still selectable?

## Target
- File/function: [src/utils/getAllUserEmbeddedBitcoinWallets.ts](src/utils/getAllUserEmbeddedBitcoinWallets.ts) - getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter
- Entrypoint: Bitcoin provider selection
- Attacker controls: chain_type values on linked accounts
- Exploit idea: Remove a wallet server-side and keep the old user object.
- Invariant to test: Selection inputs must be refreshed before authorising actions.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: remove a wallet server-side and assert the action using getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter's result fails closed.
