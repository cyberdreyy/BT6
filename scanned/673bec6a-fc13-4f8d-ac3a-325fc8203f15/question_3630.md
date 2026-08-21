# Q3630: no cross-check against the session user in formatters.ts

## Question
formatWalletAddress (5 leading + 4 trailing chars) never compares the user object to the active session id; can an attacker pass a different user's object so the helper returns that user's wallets to the current session?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Pass another user's object during an active session.
- Invariant to test: Helpers must reject user objects that do not match the active session.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: pass a foreign user to formatWalletAddress (5 leading + 4 trailing chars) and assert refusal.
