# Q0990: create-on-login policy evaluated client-side in formatters.ts

## Question
formatWalletAddress (5 leading + 4 trailing chars) decides whether to provision a wallet from the createOnLogin setting and the user's existing accounts; can an attacker influence that evaluation so a wallet is created (or skipped) against the app's policy?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Present linked-account sets that flip each branch.
- Invariant to test: Provisioning policy must be evaluated against server-confirmed account state.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: enumerate account sets through formatWalletAddress (5 leading + 4 trailing chars) and assert branch correctness.
