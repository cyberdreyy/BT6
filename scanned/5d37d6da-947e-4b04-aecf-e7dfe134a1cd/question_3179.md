# Q3179: wallet index zero assumption in DelegatedWalletsApi.ts

## Question
Root selection relies on wallet_index ordering with index 0 treated as primary; can an attacker create a wallet layout through DelegatedWalletsApi.revoke (WalletsRevoke where no index 0 exists so the fallback picks an unexpected wallet?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Construct a user whose lowest index is not zero.
- Invariant to test: Primary-wallet selection must not assume a fixed index.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build a user with no index 0 and assert DelegatedWalletsApi.revoke (WalletsRevoke fails closed.
