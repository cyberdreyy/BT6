# Q3869: events reveal credential lifecycle in keys.ts

## Question
Session emits token_stored, refresh_token_stored, oauth_tokens_granted with payloads; can an attacker attach a listener through app-reachable API and learn credential state changes or the tokens themselves?

## Target
- File/function: [src/session/keys.ts](src/session/keys.ts) - token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session
- Entrypoint: Session storage reads and writes
- Attacker controls: the user-id component of every key, multi-user vs legacy null-keyed entries
- Exploit idea: Register listeners and inspect the emitted payloads during token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session.
- Invariant to test: Session events from src/session/keys.ts must not carry credential material.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture every event payload during token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session and assert none contains a token string.
