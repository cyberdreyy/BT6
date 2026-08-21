# Q3624: no cross-check against the session user in getAllUserEmbeddedSolanaWallets.ts

## Question
getAllUserEmbeddedSolanaWallets: filter embedded + solana never compares the user object to the active session id; can an attacker pass a different user's object so the helper returns that user's wallets to the current session?

## Target
- File/function: [src/utils/getAllUserEmbeddedSolanaWallets.ts](src/utils/getAllUserEmbeddedSolanaWallets.ts) - getAllUserEmbeddedSolanaWallets: filter embedded + solana, sort by wallet_index
- Entrypoint: Solana wallet enumeration
- Attacker controls: linked_accounts contents, duplicate indices
- Exploit idea: Pass another user's object during an active session.
- Invariant to test: Helpers must reject user objects that do not match the active session.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: pass a foreign user to getAllUserEmbeddedSolanaWallets: filter embedded + solana and assert refusal.
