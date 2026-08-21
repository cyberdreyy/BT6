# Q3729: delegation errors surface wallet addresses in DelegatedWalletsApi.ts

## Question
Error paths embed the address being delegated; can an attacker use DelegatedWalletsApi.revoke (WalletsRevoke to extract another user's address from a shared error surface?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Trigger errors with candidate addresses and read the messages.
- Invariant to test: Errors must not echo identifiers the caller did not supply.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert DelegatedWalletsApi.revoke (WalletsRevoke does not echo unrelated addresses.
