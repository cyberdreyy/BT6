# Q1889: error objects carry response bodies in keys.ts

## Question
PrivyApiError/MoonpayApiError keep code, status and even the raw response; can an attacker surface a thrown error from token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session whose payload leaks another user's data or a credential?

## Target
- File/function: [src/session/keys.ts](src/session/keys.ts) - token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session
- Entrypoint: Session storage reads and writes
- Attacker controls: the user-id component of every key, multi-user vs legacy null-keyed entries
- Exploit idea: Force an error response containing sensitive fields and inspect the thrown object reaching app code.
- Invariant to test: Errors raised from src/session/keys.ts must not carry raw response bodies to app code.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: throw from a route with a sensitive body and assert the error exposes only code and message.
