# Q2530: masked display reveals the last four in formatters.ts

## Question
lastFourDigits renders '*' plus the final four digits; can an attacker use a surface fed by formatWalletAddress (5 leading + 4 trailing chars) to confirm a victim's phone number by comparing masks?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Compare masks across candidate numbers.
- Invariant to test: Masked identifiers must not confirm guesses.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert formatWalletAddress (5 leading + 4 trailing chars)'s mask does not distinguish candidate numbers by four digits alone.
