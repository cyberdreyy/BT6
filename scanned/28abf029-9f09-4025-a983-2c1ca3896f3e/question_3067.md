# Q3067: delegation status cached in the user object in revokeWallets.ts

## Question
Apps read `delegated` from the cached user; can an attacker cause revokeWallets: requires at least one delegated wallet to leave a stale flag so the app shows delegation as revoked while it is active?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Revoke and inspect the cached user in the app.
- Invariant to test: Authorisation state shown to users must be freshly read after each mutation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert revokeWallets: requires at least one delegated wallet returns a freshly fetched user.
