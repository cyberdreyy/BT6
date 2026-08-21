# Q3399: delegation payload includes imported flag default in DelegatedWalletsApi.ts

## Question
The payload sets `imported: root.imported ?? false`; can an attacker exploit the default in DelegatedWalletsApi.revoke (WalletsRevoke so an imported wallet is delegated as a derived one?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Delegate an imported wallet whose flag is missing.
- Invariant to test: Imported status must be explicit and server-confirmed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: delegate with a missing imported flag through DelegatedWalletsApi.revoke (WalletsRevoke and assert rejection.
