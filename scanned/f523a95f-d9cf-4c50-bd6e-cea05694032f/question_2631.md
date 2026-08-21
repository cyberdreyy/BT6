# Q2631: typing formatter leaks partial state in getUserEmbeddedEthereumWallet.ts

## Question
phoneNumberTypingFormatter builds an AsYouType instance per country; can an attacker exploit its retained state through getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 so a previously typed number influences the formatted result of the next?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Reuse the formatter across two inputs.
- Invariant to test: Formatter state must not persist between inputs.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: reuse a getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 formatter and assert no state carries over.
