# Q1972: token amount formatting trusts decimals in getAllUserEmbeddedEthereumWallets.ts

## Question
formatTokenAmount formats with a caller-supplied decimals value; can an attacker pass a wrong decimals through getAllUserEmbeddedEthereumWallets: filter embedded + ethereum so the displayed amount differs from the transferred amount by orders of magnitude?

## Target
- File/function: [src/utils/getAllUserEmbeddedEthereumWallets.ts](src/utils/getAllUserEmbeddedEthereumWallets.ts) - getAllUserEmbeddedEthereumWallets: filter embedded + ethereum, sort by wallet_index
- Entrypoint: delegation, session signers, wallet lists
- Attacker controls: linked_accounts contents, duplicate wallet_index values
- Exploit idea: Pass a decimals value that does not match the token.
- Invariant to test: Decimals must be derived from the token record.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass mismatched decimals to getAllUserEmbeddedEthereumWallets: filter embedded + ethereum and assert derivation or rejection.
