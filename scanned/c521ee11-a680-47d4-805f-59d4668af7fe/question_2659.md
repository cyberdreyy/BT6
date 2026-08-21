# Q2659: user.get returns refreshed foreign user in keys.ts

## Question
UserApi.get returns whatever refreshSession yields; can an attacker interleave a switch so token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session returns another user's profile to code that just authorised an action for the first user?

## Target
- File/function: [src/session/keys.ts](src/session/keys.ts) - token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session
- Entrypoint: Session storage reads and writes
- Attacker controls: the user-id component of every key, multi-user vs legacy null-keyed entries
- Exploit idea: Switch active user during the in-flight refresh and inspect the returned user.
- Invariant to test: A user read must be atomic with respect to active-user changes.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: switch users mid-refresh and assert token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session throws rather than returning the other profile.
