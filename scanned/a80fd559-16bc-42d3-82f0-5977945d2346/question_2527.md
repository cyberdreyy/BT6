# Q2527: masked display reveals the last four in shouldCreateEmbeddedEthWallet.ts

## Question
lastFourDigits renders '*' plus the final four digits; can an attacker use a surface fed by shouldCreateEmbeddedEthWallet(user to confirm a victim's phone number by comparing masks?

## Target
- File/function: [src/utils/shouldCreateEmbeddedEthWallet.ts](src/utils/shouldCreateEmbeddedEthWallet.ts) - shouldCreateEmbeddedEthWallet(user, createOnLogin: 'off'|'users-without-wallets'|'all-users')
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: external wallets linked to the account and the createOnLogin setting
- Exploit idea: Compare masks across candidate numbers.
- Invariant to test: Masked identifiers must not confirm guesses.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert shouldCreateEmbeddedEthWallet(user's mask does not distinguish candidate numbers by four digits alone.
