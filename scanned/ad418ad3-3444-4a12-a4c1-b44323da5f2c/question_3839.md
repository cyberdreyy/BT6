# Q3839: delegate before wallet exists in DelegatedWalletsApi.ts

## Question
delegateWallet can be called before the embedded wallet finishes provisioning; can an attacker use DelegatedWalletsApi.revoke (WalletsRevoke in that window so delegation binds to a wallet record that changes afterwards?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Call delegate during wallet creation.
- Invariant to test: Delegation must require a fully provisioned, confirmed wallet.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: call DelegatedWalletsApi.revoke (WalletsRevoke during provisioning and assert refusal.
