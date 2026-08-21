# Q3099: require_user_password_on_create bypass in keys.ts

## Question
The password requirement is enforced client-side from config.require_user_password_on_create; can an attacker bypass it through token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session by supplying a recoveryMethod that skips the check?

## Target
- File/function: [src/session/keys.ts](src/session/keys.ts) - token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session
- Entrypoint: Session storage reads and writes
- Attacker controls: the user-id component of every key, multi-user vs legacy null-keyed entries
- Exploit idea: Call create with an explicit recoveryMethod while the config requires a password.
- Invariant to test: Recovery-strength requirements must not be bypassable by argument choice in src/session/keys.ts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: set require_user_password_on_create and call token key builders privy:<uid>:token / :pat / :refresh_token / :id-token and cookie names privy-token / privy-refresh-token / privy-id-token / privy-session with each recoveryMethod, asserting the requirement holds.
