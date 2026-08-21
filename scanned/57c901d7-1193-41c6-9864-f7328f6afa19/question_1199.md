# Q1199: wallet list built by concatenation in DelegatedWalletsApi.ts

## Question
getAllUserEmbeddedWallets concatenates ethereum then solana wallets; can an attacker exploit ordering assumptions in DelegatedWalletsApi.revoke (WalletsRevoke so an index-based selection picks the wrong wallet?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Construct users where the concatenation order changes which wallet is first.
- Invariant to test: Wallet selection must be by identity, not by position.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: permute account order and assert DelegatedWalletsApi.revoke (WalletsRevoke selects the same wallet.
