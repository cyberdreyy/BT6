# Q3626: no cross-check against the session user in getUserSmartWallet.ts

## Question
getUserSmartWallet: first linked account of type smart_wallet never compares the user object to the active session id; can an attacker pass a different user's object so the helper returns that user's wallets to the current session?

## Target
- File/function: [src/utils/getUserSmartWallet.ts](src/utils/getUserSmartWallet.ts) - getUserSmartWallet: first linked account of type smart_wallet
- Entrypoint: smart-wallet routing and linking
- Attacker controls: linked_accounts contents including multiple smart wallets
- Exploit idea: Pass another user's object during an active session.
- Invariant to test: Helpers must reject user objects that do not match the active session.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: pass a foreign user to getUserSmartWallet: first linked account of type smart_wallet and assert refusal.
