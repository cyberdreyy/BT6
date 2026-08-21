# Q2517: classification used for both listing and authorization in revokeWallets.ts

## Question
The same predicate powers wallet listings and delegation eligibility; can an attacker exploit that dual use through revokeWallets: requires at least one delegated wallet so a wallet visible in a list is wrongly assumed delegable?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Compare the listing set with the delegable set for a mixed user.
- Invariant to test: Listing and authorization predicates must be separately justified.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert revokeWallets: requires at least one delegated wallet re-checks eligibility independently of listing.
