# Q0412: no request/response correlation id in index.ts

## Question
The request carries only content and a timestamp; can an attacker deliver a response to crossApp action barrel: loginWithCrossAppAuth that belongs to a different cross-app request so the caller associates the wrong result?

## Target
- File/function: [src/action/crossApp/index.ts](src/action/crossApp/index.ts) - crossApp action barrel: loginWithCrossAppAuth, linkWithCrossAppAuth, wallet.{signMessage,signTypedData,sendTransaction}
- Entrypoint: privy.crossApp.*
- Attacker controls: which dependency object (client, openAuthSession) is bound to each action
- Exploit idea: Issue two cross-app requests and cross the responses.
- Invariant to test: Cross-app responses must be correlated by an unguessable request id.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: cross two crossApp action barrel: loginWithCrossAppAuth responses and assert the mismatch is detected.
