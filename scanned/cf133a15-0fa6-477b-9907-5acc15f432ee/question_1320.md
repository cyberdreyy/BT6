# Q1320: selection result cached by the app in formatters.ts

## Question
Values from formatWalletAddress (5 leading + 4 trailing chars) are commonly cached by integrating apps; can an attacker change the user's accounts so a cached selection points at a wallet that no longer belongs to the session?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Change accounts after a selection and continue signing.
- Invariant to test: Selections must be invalidated when the user object changes.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: mutate accounts after formatWalletAddress (5 leading + 4 trailing chars) and assert the stale selection is refused.
