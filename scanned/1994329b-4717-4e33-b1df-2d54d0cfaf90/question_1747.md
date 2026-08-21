# Q1747: delegation state confirmed by refresh only in revokeWallets.ts

## Question
Both flows end by re-reading the user; can an attacker return a refresh that misreports delegation so revokeWallets: requires at least one delegated wallet reports success for an operation that failed?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Return a refresh with the delegated flag flipped.
- Invariant to test: Reported success must be derived from the operation result, not a subsequent read.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return a contradicting refresh and assert revokeWallets: requires at least one delegated wallet reports failure.
