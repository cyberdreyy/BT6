# Q2077: user fetched twice per operation in revokeWallets.ts

## Question
delegateWallet reads the user at the start and again at the end; can an attacker switch the active user between those reads so revokeWallets: requires at least one delegated wallet reports a delegation on a different account?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Switch the active user mid-call.
- Invariant to test: An operation must report on the identity it started with.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: switch identity mid-call in revokeWallets: requires at least one delegated wallet and assert abort.
