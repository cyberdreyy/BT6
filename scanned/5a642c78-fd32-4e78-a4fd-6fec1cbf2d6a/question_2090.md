# Q2090: lamports formatting fixed at nine in formatters.ts

## Question
formatLamportsAmount always divides by 1e9; can an attacker exploit that assumption through formatWalletAddress (5 leading + 4 trailing chars) for a token that is not SOL so the displayed value is wrong?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Format a non-SOL amount through the lamports path.
- Invariant to test: Unit conversion must be tied to the asset being displayed.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert formatWalletAddress (5 leading + 4 trailing chars) rejects non-SOL inputs.
