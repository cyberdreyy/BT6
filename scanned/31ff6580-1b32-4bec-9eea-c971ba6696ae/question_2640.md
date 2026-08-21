# Q2640: typing formatter leaks partial state in formatters.ts

## Question
phoneNumberTypingFormatter builds an AsYouType instance per country; can an attacker exploit its retained state through formatWalletAddress (5 leading + 4 trailing chars) so a previously typed number influences the formatted result of the next?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Reuse the formatter across two inputs.
- Invariant to test: Formatter state must not persist between inputs.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: reuse a formatWalletAddress (5 leading + 4 trailing chars) formatter and assert no state carries over.
