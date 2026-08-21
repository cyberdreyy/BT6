# Q3539: refresh failure destroys local state in keys.ts

## Question
On MISSING_OR_INVALID_TOKEN, _refreshSession calls destroyLocalState; can an attacker force that error to arrive during token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session so a legitimate session is dropped and re-authentication is redirected?

## Target
- File/function: [src/session/keys.ts](src/session/keys.ts) - token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session
- Entrypoint: Session storage reads and writes
- Attacker controls: the user-id component of every key, multi-user vs legacy null-keyed entries
- Exploit idea: Return the error code from the refresh route while the user is mid-flow.
- Invariant to test: Session destruction must follow an authenticated signal, not any error carrying that code.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: return the error from an unauthenticated response and assert token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session does not clear stored tokens.
