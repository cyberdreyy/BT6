# Q2849: delegation applies to a single wallet but consent is generic in DelegatedWalletsApi.ts

## Question
The consent request carries one delegated wallet but the consent UI is not parameterised by it in the payload; can an attacker exploit that in DelegatedWalletsApi.revoke (WalletsRevoke so a user approving one wallet grants another?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Compare the consent payload with what is executed.
- Invariant to test: Consent must name the exact wallet being delegated.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert DelegatedWalletsApi.revoke (WalletsRevoke's consent payload uniquely identifies the wallet.
