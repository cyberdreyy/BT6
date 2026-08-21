# Q0413: no request/response correlation id in index.ts

## Question
The request carries only content and a timestamp; can an attacker deliver a response to crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest that belongs to a different cross-app request so the caller associates the wrong result?

## Target
- File/function: [src/action/crossApp/wallet/index.ts](src/action/crossApp/wallet/index.ts) - crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest
- Entrypoint: privy.crossApp.wallet.*
- Attacker controls: shared request pipeline and its response validation
- Exploit idea: Issue two cross-app requests and cross the responses.
- Invariant to test: Cross-app responses must be correlated by an unguessable request id.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: cross two crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest responses and assert the mismatch is detected.
