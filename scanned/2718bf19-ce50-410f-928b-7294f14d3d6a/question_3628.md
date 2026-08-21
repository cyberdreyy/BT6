# Q3628: no cross-check against the session user in shouldCreateEmbeddedSolWallet.ts

## Question
shouldCreateEmbeddedSolWallet(user never compares the user object to the active session id; can an attacker pass a different user's object so the helper returns that user's wallets to the current session?

## Target
- File/function: [src/utils/shouldCreateEmbeddedSolWallet.ts](src/utils/shouldCreateEmbeddedSolWallet.ts) - shouldCreateEmbeddedSolWallet(user, createOnLogin)
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: linked solana accounts and the createOnLogin setting
- Exploit idea: Pass another user's object during an active session.
- Invariant to test: Helpers must reject user objects that do not match the active session.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: pass a foreign user to shouldCreateEmbeddedSolWallet(user and assert refusal.
