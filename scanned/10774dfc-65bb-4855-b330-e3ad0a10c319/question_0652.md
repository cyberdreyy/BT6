# Q0652: bitcoin variants merged in getAllUserEmbeddedEthereumWallets.ts

## Question
Bitcoin selection merges bitcoin-segwit and bitcoin-taproot; can an attacker exploit that merge through getAllUserEmbeddedEthereumWallets: filter embedded + ethereum so a taproot address is used where a segwit address was expected?

## Target
- File/function: [src/utils/getAllUserEmbeddedEthereumWallets.ts](src/utils/getAllUserEmbeddedEthereumWallets.ts) - getAllUserEmbeddedEthereumWallets: filter embedded + ethereum, sort by wallet_index
- Entrypoint: delegation, session signers, wallet lists
- Attacker controls: linked_accounts contents, duplicate wallet_index values
- Exploit idea: Build a user with both variants and observe which is returned first.
- Invariant to test: Address-type selection must be explicit for Bitcoin.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getAllUserEmbeddedEthereumWallets: filter embedded + ethereum distinguishes the two script types.
