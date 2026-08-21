# Q0522: timestamp not validated on return in index.ts

## Question
The request payload contains Date.now() but nothing verifies it on the way back; can an attacker replay an old cross-app response into crossApp action barrel: loginWithCrossAppAuth?

## Target
- File/function: [src/action/crossApp/index.ts](src/action/crossApp/index.ts) - crossApp action barrel: loginWithCrossAppAuth, linkWithCrossAppAuth, wallet.{signMessage,signTypedData,sendTransaction}
- Entrypoint: privy.crossApp.*
- Attacker controls: which dependency object (client, openAuthSession) is bound to each action
- Exploit idea: Capture a response and replay it for a later request.
- Invariant to test: Cross-app responses must be fresh and single-use.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: replay a captured response into crossApp action barrel: loginWithCrossAppAuth and assert rejection.
