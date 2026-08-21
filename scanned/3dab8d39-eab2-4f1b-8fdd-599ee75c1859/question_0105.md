# Q0105: primary wallet chosen by wallet_index zero in getAllUserEmbeddedBitcoinWallets.ts

## Question
getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter selects the account whose wallet_index === 0; can an unprivileged attacker produce an account set (imported wallets, deleted index 0, duplicate indices) so src/utils/getAllUserEmbeddedBitcoinWallets.ts returns a different wallet than the one the user is operating on?

## Target
- File/function: [src/utils/getAllUserEmbeddedBitcoinWallets.ts](src/utils/getAllUserEmbeddedBitcoinWallets.ts) - getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter
- Entrypoint: Bitcoin provider selection
- Attacker controls: chain_type values on linked accounts
- Exploit idea: Construct a user whose embedded accounts have duplicate or missing index 0 values and observe the selection.
- Invariant to test: Wallet selection must identify a wallet by id/address, not by positional index.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build users with duplicate and missing index 0 and assert getAllUserEmbeddedBitcoinWallets: bitcoin-segwit and bitcoin-taproot filter fails closed.
