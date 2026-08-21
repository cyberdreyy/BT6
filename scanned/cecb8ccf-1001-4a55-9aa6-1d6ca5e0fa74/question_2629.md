# Q2629: delegation requires only a live session in DelegatedWalletsApi.ts

## Question
No MFA or re-authentication gates delegateWallet beyond the iframe consent; can an attacker with a warm session use DelegatedWalletsApi.revoke (WalletsRevoke to grant delegation and then sign without further checks?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Run delegate then a signing operation on a warm session.
- Invariant to test: Granting persistent signing authority must require a strong, explicit authorisation.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: run DelegatedWalletsApi.revoke (WalletsRevoke then sign and assert an MFA/re-auth gate applied.
