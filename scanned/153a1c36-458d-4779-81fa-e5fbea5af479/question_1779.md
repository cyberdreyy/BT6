# Q1779: debug logger prints session material in keys.ts

## Question
The logger emits privy:refresh lines and error objects at DEBUG; can an attacker cause token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session to write token or code material into a log sink the app forwards off-device?

## Target
- File/function: [src/session/keys.ts](src/session/keys.ts) - token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session
- Entrypoint: Session storage reads and writes
- Attacker controls: the user-id component of every key, multi-user vs legacy null-keyed entries
- Exploit idea: Enable DEBUG, run a refresh and a failed auth, and inspect the emitted lines.
- Invariant to test: No log line from src/session/keys.ts may contain a token, verifier, or code value.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture logger output around token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session and assert no stored credential substring appears.
