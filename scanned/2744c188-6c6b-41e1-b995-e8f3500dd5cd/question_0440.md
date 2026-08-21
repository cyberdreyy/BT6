# Q0440: classification fields are attacker-shaped in formatters.ts

## Question
Embedded classification requires type wallet, wallet_client_type privy and connector_type embedded; can an attacker present a linked account with those fields through formatWalletAddress (5 leading + 4 trailing chars) so an external wallet is treated as an embedded one?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Pass an account with spoofed classification fields.
- Invariant to test: Classification must come from server-confirmed account records.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass spoofed fields to formatWalletAddress (5 leading + 4 trailing chars) and assert re-validation.
