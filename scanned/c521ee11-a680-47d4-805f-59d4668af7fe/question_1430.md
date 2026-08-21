# Q1430: linked_accounts order is server supplied in formatters.ts

## Question
formatWalletAddress (5 leading + 4 trailing chars) depends on the order of user.linked_accounts as returned by the API; can an attacker influence that order so a different wallet becomes primary?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Return the same accounts in a different order and compare selections.
- Invariant to test: Selection must be order-independent.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: permute the account list and assert formatWalletAddress (5 leading + 4 trailing chars) returns the same wallet.
