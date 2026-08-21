# Q2526: masked display reveals the last four in getUserSmartWallet.ts

## Question
lastFourDigits renders '*' plus the final four digits; can an attacker use a surface fed by getUserSmartWallet: first linked account of type smart_wallet to confirm a victim's phone number by comparing masks?

## Target
- File/function: [src/utils/getUserSmartWallet.ts](src/utils/getUserSmartWallet.ts) - getUserSmartWallet: first linked account of type smart_wallet
- Entrypoint: smart-wallet routing and linking
- Attacker controls: linked_accounts contents including multiple smart wallets
- Exploit idea: Compare masks across candidate numbers.
- Invariant to test: Masked identifiers must not confirm guesses.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert getUserSmartWallet: first linked account of type smart_wallet's mask does not distinguish candidate numbers by four digits alone.
