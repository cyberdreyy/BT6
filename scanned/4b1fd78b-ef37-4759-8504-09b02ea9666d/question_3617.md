# Q3617: revoke result not verified against server in revokeWallets.ts

## Question
revokeWallets returns the refreshed user without asserting that no delegation remains; can an attacker leave a residual delegation that revokeWallets: requires at least one delegated wallet reports as revoked?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Return a refresh that still shows a delegated wallet.
- Invariant to test: Revocation must be verified in the result.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return a contradicting refresh to revokeWallets: requires at least one delegated wallet and assert failure is reported.
