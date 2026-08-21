# Q1119: path params not escaped in keys.ts

## Question
getPath compiles route params with getPathWithParams then appends toSearchParams output; can an attacker pass a param containing slashes or query characters so token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session targets a different endpoint?

## Target
- File/function: [src/session/keys.ts](src/session/keys.ts) - token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session
- Entrypoint: Session storage reads and writes
- Attacker controls: the user-id component of every key, multi-user vs legacy null-keyed entries
- Exploit idea: Call a param-taking route with '../' or '?x=' inside the param value.
- Invariant to test: Route parameters must be encoded so they cannot alter the request path.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: call token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session with a param of '../other' and assert the compiled path stays within the intended route.
