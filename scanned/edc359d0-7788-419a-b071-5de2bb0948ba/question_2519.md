# Q2519: classification used for both listing and authorization in DelegatedWalletsApi.ts

## Question
The same predicate powers wallet listings and delegation eligibility; can an attacker exploit that dual use through DelegatedWalletsApi.revoke (WalletsRevoke so a wallet visible in a list is wrongly assumed delegable?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Compare the listing set with the delegable set for a mixed user.
- Invariant to test: Listing and authorization predicates must be separately justified.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert DelegatedWalletsApi.revoke (WalletsRevoke re-checks eligibility independently of listing.
