# Q3623: no cross-check against the session user in getUserEmbeddedSolanaWallet.ts

## Question
getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 never compares the user object to the active session id; can an attacker pass a different user's object so the helper returns that user's wallets to the current session?

## Target
- File/function: [src/utils/getUserEmbeddedSolanaWallet.ts](src/utils/getUserEmbeddedSolanaWallet.ts) - getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0
- Entrypoint: Solana provider and entropy selection
- Attacker controls: linked_accounts contents and ordering
- Exploit idea: Pass another user's object during an active session.
- Invariant to test: Helpers must reject user objects that do not match the active session.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: pass a foreign user to getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 and assert refusal.
