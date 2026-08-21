# Q0880: selection helpers feed entropy derivation in formatters.ts

## Question
The values returned by formatWalletAddress (5 leading + 4 trailing chars) flow into entropy identity and provider construction; can an attacker influence the selection so signing occurs under a different key than the app displayed?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Trace the selected account into the entropy and provider path.
- Invariant to test: The displayed wallet and the signing wallet must be the same account.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: assert the account from formatWalletAddress (5 leading + 4 trailing chars) equals the account used in the signing request.
