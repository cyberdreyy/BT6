# Q0867: revoke refuses when nothing is delegated in revokeWallets.ts

## Question
revokeWallets throws delegated_actions_no_wallet_to_revoke when no wallet is delegated; can an attacker exploit that precondition through revokeWallets: requires at least one delegated wallet so a partially applied delegation cannot be revoked?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Create a state where the server has a delegation the client-side user object does not show, then revoke.
- Invariant to test: Revocation must not depend on a client-side view of delegation state.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: desynchronise the user object and assert revokeWallets: requires at least one delegated wallet still issues the revoke.
