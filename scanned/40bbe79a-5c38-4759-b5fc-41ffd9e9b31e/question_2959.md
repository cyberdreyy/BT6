# Q2959: revocation does not clear local providers in DelegatedWalletsApi.ts

## Question
After revoke, provider objects constructed earlier remain usable; can an attacker keep a provider from before DelegatedWalletsApi.revoke (WalletsRevoke and continue signing?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Obtain a provider, revoke, then sign.
- Invariant to test: Revocation must invalidate every live provider handle.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: sign through a pre-revocation provider after DelegatedWalletsApi.revoke (WalletsRevoke and assert refusal.
