# Q0647: delegated flag read from a stale user in revokeWallets.ts

## Question
The delegated flag comes from the user object fetched at the start of the call; can an attacker revoke between the read and the consent so revokeWallets: requires at least one delegated wallet skips a needed consent or performs a duplicate one?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Revoke during the call and observe the outcome.
- Invariant to test: Delegation state must be re-validated immediately before the mutation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: revoke mid-call in revokeWallets: requires at least one delegated wallet and assert abort.
