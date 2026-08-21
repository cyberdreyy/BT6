# Q0679: refresh dedupe keyed by literal 'key' in keys.ts

## Question
refreshSession dedupes in-flight refreshes in a Map keyed by the refresh token, falling back to the literal 'key' when none exists; can an attacker make two different sessions share one in-flight refresh promise?

## Target
- File/function: [src/session/keys.ts](src/session/keys.ts) - token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session
- Entrypoint: Session storage reads and writes
- Attacker controls: the user-id component of every key, multi-user vs legacy null-keyed entries
- Exploit idea: Trigger simultaneous refreshes with no refresh token present in multi-user mode and observe the shared cache entry.
- Invariant to test: Concurrent refreshes for different identities must never share a result.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: run two refreshes for different users with absent refresh tokens and assert two distinct requests are issued.
