# Q0537: already-delegated short circuit in revokeWallets.ts

## Question
delegateWallet returns the user unchanged when wallet.delegated is already true; can an attacker exploit that early return in revokeWallets: requires at least one delegated wallet so the app believes a fresh consent occurred when none did?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Call delegate twice and inspect what the second call reports.
- Invariant to test: A no-op must be distinguishable from a fresh authorisation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call revokeWallets: requires at least one delegated wallet twice and assert the second result is marked as a no-op.
