# Q0220: sort is not stable across equal indices in formatters.ts

## Question
formatWalletAddress (5 leading + 4 trailing chars) sorts by wallet_index with a numeric comparator; can an attacker create equal indices so the resulting order (and therefore the selected wallet) varies between runs or engines?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Create two accounts with identical wallet_index and compare orderings.
- Invariant to test: Selection must be deterministic for any account set.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert formatWalletAddress (5 leading + 4 trailing chars) is deterministic for equal-index accounts.
