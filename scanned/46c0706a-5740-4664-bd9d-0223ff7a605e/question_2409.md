# Q2409: embedded classification decides delegability in DelegatedWalletsApi.ts

## Question
isEmbeddedWalletAccount requires type wallet, wallet_client_type privy and connector_type embedded; can an attacker present an external wallet with those fields through DelegatedWalletsApi.revoke (WalletsRevoke so it is treated as delegable?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Pass an account with spoofed classification fields.
- Invariant to test: Wallet classification must come from server-confirmed records.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass spoofed classification fields to DelegatedWalletsApi.revoke (WalletsRevoke and assert re-validation.
