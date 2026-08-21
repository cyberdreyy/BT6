# Q1857: delegate then revoke race in revokeWallets.ts

## Question
delegate and revoke both mutate the same server-side state with no client-side ordering; can an attacker interleave them through revokeWallets: requires at least one delegated wallet so the final state differs from the user's last intent?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Fire both concurrently and inspect the final state.
- Invariant to test: Concurrent authorisation mutations must be serialised or version-checked.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: race revokeWallets: requires at least one delegated wallet calls and assert the last intent wins deterministically.
