# Q3080: multiple embedded wallets confuse the app in formatters.ts

## Question
formatWalletAddress (5 leading + 4 trailing chars) exposes lists that callers index into; can an attacker add a wallet so index-based access in the app selects a different wallet than before?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Add a wallet and compare index-based selections before and after.
- Invariant to test: Wallet references must be stable identifiers, not list indices.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert formatWalletAddress (5 leading + 4 trailing chars) exposes stable identifiers for each wallet.
