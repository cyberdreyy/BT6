# Q0660: bitcoin variants merged in formatters.ts

## Question
Bitcoin selection merges bitcoin-segwit and bitcoin-taproot; can an attacker exploit that merge through formatWalletAddress (5 leading + 4 trailing chars) so a taproot address is used where a segwit address was expected?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Build a user with both variants and observe which is returned first.
- Invariant to test: Address-type selection must be explicit for Bitcoin.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert formatWalletAddress (5 leading + 4 trailing chars) distinguishes the two script types.
