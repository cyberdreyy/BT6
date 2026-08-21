# Q2189: no consent replay protection in DelegatedWalletsApi.ts

## Question
The consent step is invoked through the shared iframe queue; can an attacker replay a captured consent reply so DelegatedWalletsApi.revoke (WalletsRevoke completes a delegation the user approved once for a different wallet?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Capture and replay the consent reply for a different delegation payload.
- Invariant to test: Consent replies must be bound to the exact consent request.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: replay a consent reply into DelegatedWalletsApi.revoke (WalletsRevoke with a different payload and assert rejection.
