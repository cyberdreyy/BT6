# Q2638: typing formatter leaks partial state in shouldCreateEmbeddedSolWallet.ts

## Question
phoneNumberTypingFormatter builds an AsYouType instance per country; can an attacker exploit its retained state through shouldCreateEmbeddedSolWallet(user so a previously typed number influences the formatted result of the next?

## Target
- File/function: [src/utils/shouldCreateEmbeddedSolWallet.ts](src/utils/shouldCreateEmbeddedSolWallet.ts) - shouldCreateEmbeddedSolWallet(user, createOnLogin)
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: linked solana accounts and the createOnLogin setting
- Exploit idea: Reuse the formatter across two inputs.
- Invariant to test: Formatter state must not persist between inputs.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: reuse a shouldCreateEmbeddedSolWallet(user formatter and assert no state carries over.
