# Q3410: selection result feeds funding destinations in formatters.ts

## Question
Funding and deposit flows use formatWalletAddress (5 leading + 4 trailing chars) to choose a destination; can an attacker influence the selection so funds arrive at a wallet the user did not intend?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Change the account set before a funding flow.
- Invariant to test: Funding destinations must be user-confirmed, not derived by a helper.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Integration test: change accounts before funding and assert confirmation is re-requested.
