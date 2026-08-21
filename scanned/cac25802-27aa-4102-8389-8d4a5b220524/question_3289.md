# Q3289: solana fallback for an ethereum request in DelegatedWalletsApi.ts

## Question
getRootWallet falls back to the first solana wallet when no ethereum wallet exists; can an attacker exploit that cross-chain fallback in DelegatedWalletsApi.revoke (WalletsRevoke so an ethereum delegation is rooted in a solana wallet?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Delegate an ethereum wallet for a user with only solana embedded wallets.
- Invariant to test: Root and delegated wallets must belong to a compatible custody root.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert DelegatedWalletsApi.revoke (WalletsRevoke refuses cross-chain root fallback.
