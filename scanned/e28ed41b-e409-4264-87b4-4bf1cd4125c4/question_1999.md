# Q1999: appId or clientId swapped at construction in keys.ts

## Question
Privy's constructor accepts appId, clientId, baseUrl, storage and crypto; can an attacker in the page reach token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session with substituted options so requests are signed and stored under a different app namespace?

## Target
- File/function: [src/session/keys.ts](src/session/keys.ts) - token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session
- Entrypoint: Session storage reads and writes
- Attacker controls: the user-id component of every key, multi-user vs legacy null-keyed entries
- Exploit idea: Construct a second client with a different appId sharing the same storage and observe key collisions.
- Invariant to test: Storage namespacing must prevent one app id's session from being consumed by another.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: run two clients with different appIds over one Storage and assert no key collisions.
