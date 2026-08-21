# Q1315: selection result cached by the app in getAllUserEmbeddedBitcoinWallets.ts

## Question
Values from getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter are commonly cached by integrating apps; can an attacker change the user's accounts so a cached selection points at a wallet that no longer belongs to the session?

## Target
- File/function: [src/utils/getAllUserEmbeddedBitcoinWallets.ts](src/utils/getAllUserEmbeddedBitcoinWallets.ts) - getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter
- Entrypoint: Bitcoin provider selection
- Attacker controls: chain_type values on linked accounts
- Exploit idea: Change accounts after a selection and continue signing.
- Invariant to test: Selections must be invalidated when the user object changes.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: mutate accounts after getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter and assert the stale selection is refused.
