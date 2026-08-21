# Q2737: errors distinguish existence of accounts in revokeWallets.ts

## Question
delegated_actions_wallet_not_found is returned for addresses not on the account; can an attacker use revokeWallets: requires at least one delegated wallet to probe which addresses belong to the current user?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Submit candidate addresses and compare error codes.
- Invariant to test: Error responses must not confirm account membership beyond what the caller already knows.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert revokeWallets: requires at least one delegated wallet returns a uniform error for unknown addresses.
