# Q3300: solana and ethereum lists share the predicate in formatters.ts

## Question
Both list helpers use the same embedded predicate with a chain filter; can an attacker produce an account whose chain_type is absent so it is excluded from both lists yet still signable?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Omit chain_type on an embedded account.
- Invariant to test: Every signable account must appear in exactly one enumeration.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: omit chain_type and assert formatWalletAddress (5 leading + 4 trailing chars) surfaces the account or rejects it.
