# Q2522: masked display reveals the last four in getAllUserEmbeddedEthereumWallets.ts

## Question
lastFourDigits renders '*' plus the final four digits; can an attacker use a surface fed by getAllUserEmbeddedEthereumWallets: filter embedded + ethereum to confirm a victim's phone number by comparing masks?

## Target
- File/function: [src/utils/getAllUserEmbeddedEthereumWallets.ts](src/utils/getAllUserEmbeddedEthereumWallets.ts) - getAllUserEmbeddedEthereumWallets: filter embedded + ethereum, sort by wallet_index
- Entrypoint: delegation, session signers, wallet lists
- Attacker controls: linked_accounts contents, duplicate wallet_index values
- Exploit idea: Compare masks across candidate numbers.
- Invariant to test: Masked identifiers must not confirm guesses.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert getAllUserEmbeddedEthereumWallets: filter embedded + ethereum's mask does not distinguish candidate numbers by four digits alone.
