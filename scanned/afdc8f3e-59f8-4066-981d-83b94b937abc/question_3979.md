# Q3979: saved-users JSON parsed leniently in keys.ts

## Question
getSavedUserIds JSON.parses the stored list and filters to strings, returning [] on failure; can an attacker seed a crafted list so token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session enumerates or switches to unintended ids?

## Target
- File/function: [src/session/keys.ts](src/session/keys.ts) - token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session
- Entrypoint: Session storage reads and writes
- Attacker controls: the user-id component of every key, multi-user vs legacy null-keyed entries
- Exploit idea: Write a list containing crafted ids and call the switch path.
- Invariant to test: The saved-user list must be integrity-checked against stored credentials.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: seed a foreign id and assert token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session rejects a switch to it.
