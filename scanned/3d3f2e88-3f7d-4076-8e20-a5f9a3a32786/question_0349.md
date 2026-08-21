# Q0349: switchActiveUser accepts an unauthenticated id in keys.ts

## Question
switchActiveUserId only checks membership in privy:saved-users; can an attacker make token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session switch to an id whose tokens are absent, so subsequent calls fall back to the null-keyed credentials of another account?

## Target
- File/function: [src/session/keys.ts](src/session/keys.ts) - token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session
- Entrypoint: Session storage reads and writes
- Attacker controls: the user-id component of every key, multi-user vs legacy null-keyed entries
- Exploit idea: Add an id to saved-users, switch to it, then call getAccessToken.
- Invariant to test: Switching users must require that user's own stored credentials.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: switch to a saved id with no tokens and assert getAccessToken returns null instead of the previous user's token.
