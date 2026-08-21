# Q3405: selection result feeds funding destinations in getAllUserEmbeddedBitcoinWallets.ts

## Question
Funding and deposit flows use getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter to choose a destination; can an attacker influence the selection so funds arrive at a wallet the user did not intend?

## Target
- File/function: [src/utils/getAllUserEmbeddedBitcoinWallets.ts](src/utils/getAllUserEmbeddedBitcoinWallets.ts) - getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter
- Entrypoint: Bitcoin provider selection
- Attacker controls: chain_type values on linked accounts
- Exploit idea: Change the account set before a funding flow.
- Invariant to test: Funding destinations must be user-confirmed, not derived by a helper.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Integration test: change accounts before funding and assert confirmation is re-requested.
