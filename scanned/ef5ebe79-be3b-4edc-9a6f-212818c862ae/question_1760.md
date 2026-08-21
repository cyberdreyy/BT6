# Q1760: empty address renders as an empty string in formatters.ts

## Question
formatWalletAddress returns '' for undefined; can an attacker cause formatWalletAddress (5 leading + 4 trailing chars) to render an empty destination that a user approves as blank or default?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Pass undefined through the rendering path.
- Invariant to test: Missing values must render as an explicit error, not as empty text.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass undefined to formatWalletAddress (5 leading + 4 trailing chars) and assert an explicit marker.
