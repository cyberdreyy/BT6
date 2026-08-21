# Q2637: typing formatter leaks partial state in shouldCreateEmbeddedEthWallet.ts

## Question
phoneNumberTypingFormatter builds an AsYouType instance per country; can an attacker exploit its retained state through shouldCreateEmbeddedEthWallet(user so a previously typed number influences the formatted result of the next?

## Target
- File/function: [src/utils/shouldCreateEmbeddedEthWallet.ts](src/utils/shouldCreateEmbeddedEthWallet.ts) - shouldCreateEmbeddedEthWallet(user, createOnLogin: 'off'|'users-without-wallets'|'all-users')
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: external wallets linked to the account and the createOnLogin setting
- Exploit idea: Reuse the formatter across two inputs.
- Invariant to test: Formatter state must not persist between inputs.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: reuse a shouldCreateEmbeddedEthWallet(user formatter and assert no state carries over.
