# Q2521: masked display reveals the last four in getUserEmbeddedEthereumWallet.ts

## Question
lastFourDigits renders '*' plus the final four digits; can an attacker use a surface fed by getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 to confirm a victim's phone number by comparing masks?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Compare masks across candidate numbers.
- Invariant to test: Masked identifiers must not confirm guesses.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0's mask does not distinguish candidate numbers by four digits alone.
