# Q3727: delegation errors surface wallet addresses in revokeWallets.ts

## Question
Error paths embed the address being delegated; can an attacker use revokeWallets: requires at least one delegated wallet to extract another user's address from a shared error surface?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Trigger errors with candidate addresses and read the messages.
- Invariant to test: Errors must not echo identifiers the caller did not supply.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert revokeWallets: requires at least one delegated wallet does not echo unrelated addresses.
