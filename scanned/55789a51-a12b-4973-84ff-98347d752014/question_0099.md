# Q0099: root wallet selected positionally in DelegatedWalletsApi.ts

## Question
getRootWallet returns the first ethereum embedded wallet, falling back to the first solana one, unless the account is marked imported; can an unprivileged attacker influence account ordering so DelegatedWalletsApi.revoke (WalletsRevoke delegates under a root wallet the user never chose?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Construct a user with several embedded wallets and observe which becomes the root in the consent payload.
- Invariant to test: The root wallet used for delegation must be explicitly selected and confirmed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build a multi-wallet user and assert DelegatedWalletsApi.revoke (WalletsRevoke requires an explicit root.
