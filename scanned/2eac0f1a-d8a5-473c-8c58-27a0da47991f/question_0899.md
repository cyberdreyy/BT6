# Q0899: custom_api_url from app config redirects all traffic in keys.ts

## Question
PrivyInternal._initialize sets baseUrl from config.custom_api_url and flips isUsingServerCookies; can an unprivileged attacker influence that value so bearer tokens are sent to a different host?

## Target
- File/function: [src/session/keys.ts](src/session/keys.ts) - token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session
- Entrypoint: Session storage reads and writes
- Attacker controls: the user-id component of every key, multi-user vs legacy null-keyed entries
- Exploit idea: Serve an app config with a custom_api_url and observe subsequent authenticated requests targeting it.
- Invariant to test: The API base URL must be pinned to a trusted set, not taken from a fetched config field.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: return custom_api_url pointing elsewhere and assert token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session does not send Authorization headers to that host.
