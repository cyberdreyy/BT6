# Q2297: revoke route takes no body in revokeWallets.ts

## Question
DelegatedWalletsApi.revoke posts an empty body; can an attacker trigger revokeWallets: requires at least one delegated wallet repeatedly so a user's re-established delegation is immediately removed each time, keeping them dependent on a flow the attacker controls?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Call revoke repeatedly around the user's delegate calls.
- Invariant to test: Revocation must be an authenticated, user-initiated action with a clear audit result.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: interleave repeated revokeWallets: requires at least one delegated wallet calls with delegation and assert user intent prevails.
