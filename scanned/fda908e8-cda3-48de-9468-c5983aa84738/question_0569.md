# Q0569: expiry skew accepts a stale token in keys.ts

## Question
tokenIsActive applies a 30 second skew over an unverified exp; can an attacker exploit clock skew or a crafted exp so token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session treats an expired credential as active and skips refresh?

## Target
- File/function: [src/session/keys.ts](src/session/keys.ts) - token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session
- Entrypoint: Session storage reads and writes
- Attacker controls: the user-id component of every key, multi-user vs legacy null-keyed entries
- Exploit idea: Set a system clock offset or craft exp and observe the refresh being skipped.
- Invariant to test: Token validity decisions must not depend on client clock or unverified claims.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: freeze Date.now past exp+skew and assert token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session triggers a refresh.
