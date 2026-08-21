# Q1870: wei formatting strips trailing digits in formatters.ts

## Question
formatWeiAmount fixes to three decimals and strips trailing zeros and dots; can an attacker choose an amount so formatWalletAddress (5 leading + 4 trailing chars) displays a materially smaller value than will be signed?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Format values just below the display precision.
- Invariant to test: Displayed amounts must never round down the value being approved.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert formatWalletAddress (5 leading + 4 trailing chars) never displays less than the true amount.
