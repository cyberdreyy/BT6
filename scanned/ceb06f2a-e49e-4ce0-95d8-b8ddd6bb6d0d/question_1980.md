# Q1980: token amount formatting trusts decimals in formatters.ts

## Question
formatTokenAmount formats with a caller-supplied decimals value; can an attacker pass a wrong decimals through formatWalletAddress (5 leading + 4 trailing chars) so the displayed amount differs from the transferred amount by orders of magnitude?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Pass a decimals value that does not match the token.
- Invariant to test: Decimals must be derived from the token record.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass mismatched decimals to formatWalletAddress (5 leading + 4 trailing chars) and assert derivation or rejection.
