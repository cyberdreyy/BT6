# Q3209: key builder collides on crafted user ids in keys.ts

## Question
Token storage keys are built by string interpolation of the user id; can an attacker obtain or seed a user id containing ':' so keys for two users collide?

## Target
- File/function: [src/session/keys.ts](src/session/keys.ts) - token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session
- Entrypoint: Session storage reads and writes
- Attacker controls: the user-id component of every key, multi-user vs legacy null-keyed entries
- Exploit idea: Store sessions for ids 'a' and 'a:token' style values and compare resulting keys.
- Invariant to test: Key construction in src/session/keys.ts must be injective over user ids.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session produces distinct keys for ids that differ only by separators.
