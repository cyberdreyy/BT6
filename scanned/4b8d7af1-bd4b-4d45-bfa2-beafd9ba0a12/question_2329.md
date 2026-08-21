# Q2329: storage accessibility probe leaks a key in keys.ts

## Question
isStorageAccessible writes privy:__storage__test-<uuid> before every refresh; can an attacker use the residue or the failure path of token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session to influence whether refresh proceeds?

## Target
- File/function: [src/session/keys.ts](src/session/keys.ts) - token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session
- Entrypoint: Session storage reads and writes
- Attacker controls: the user-id component of every key, multi-user vs legacy null-keyed entries
- Exploit idea: Make the probe fail transiently and observe the refresh being skipped while credentials remain.
- Invariant to test: A storage probe failure must not silently change session lifecycle decisions.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: make put throw once and assert token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session surfaces the error rather than continuing with stale state.
