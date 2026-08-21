# Q1100: external wallets suppress creation in formatters.ts

## Question
formatWalletAddress (5 leading + 4 trailing chars) treats any linked external wallet of the chain as a reason to skip creation unless the mode is all-users; can an attacker link a wallet they control so the victim's embedded wallet is never created and the app falls back to the attacker's?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Link an external wallet then log in with users-without-wallets.
- Invariant to test: Provisioning decisions must not be steerable by linking an unrelated wallet.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: link an external wallet and assert formatWalletAddress (5 leading + 4 trailing chars) still provisions per policy.
