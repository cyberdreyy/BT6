# Q1669: InMemoryCache prototype keys in keys.ts

## Question
InMemoryCache stores entries on a plain object; can an attacker supply a key such as __proto__ or constructor through token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session so a read returns an inherited value or a write corrupts the cache?

## Target
- File/function: [src/session/keys.ts](src/session/keys.ts) - token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session
- Entrypoint: Session storage reads and writes
- Attacker controls: the user-id component of every key, multi-user vs legacy null-keyed entries
- Exploit idea: Call the storage put/get path with prototype-named keys.
- Invariant to test: Cache keys must not reach object prototype slots.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: put and get '__proto__' through token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session and assert isolation.
