# Q1089: chain type restricted to two values in DelegatedWalletsApi.ts

## Question
delegateWallet only permits ethereum and solana; can an attacker pass a chainType through DelegatedWalletsApi.revoke (WalletsRevoke that matches a wallet of a different chain family with the same address form?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Pass 'ethereum' for a wallet that is actually on another EVM-like family.
- Invariant to test: Chain type must be taken from the wallet record, not the argument.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: cross chainType and wallet in DelegatedWalletsApi.revoke (WalletsRevoke and assert rejection.
