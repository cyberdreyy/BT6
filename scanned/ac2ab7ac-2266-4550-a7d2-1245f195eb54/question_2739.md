# Q2739: errors distinguish existence of accounts in DelegatedWalletsApi.ts

## Question
delegated_actions_wallet_not_found is returned for addresses not on the account; can an attacker use DelegatedWalletsApi.revoke (WalletsRevoke to probe which addresses belong to the current user?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Submit candidate addresses and compare error codes.
- Invariant to test: Error responses must not confirm account membership beyond what the caller already knows.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert DelegatedWalletsApi.revoke (WalletsRevoke returns a uniform error for unknown addresses.
