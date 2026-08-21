# Q3509: no rate limiting on consent prompts in DelegatedWalletsApi.ts

## Question
Each delegate call triggers an iframe consent; can an attacker drive repeated prompts through DelegatedWalletsApi.revoke (WalletsRevoke to fatigue the user into approving?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Call delegate repeatedly and count prompts.
- Invariant to test: Consent prompting must be rate-limited and deduplicated.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call DelegatedWalletsApi.revoke (WalletsRevoke repeatedly and assert prompt suppression.
