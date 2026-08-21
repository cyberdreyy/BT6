# Q0409: no request/response correlation id in signTypedData.ts

## Question
The request carries only content and a timestamp; can an attacker deliver a response to crossApp signTypedData: params [address that belongs to a different cross-app request so the caller associates the wrong result?

## Target
- File/function: [src/action/crossApp/wallet/signTypedData.ts](src/action/crossApp/wallet/signTypedData.ts) - crossApp signTypedData: params [address, generateDomainType(typedData)]
- Entrypoint: privy.crossApp.wallet.signTypedData({user, typedData, address, redirectUrl})
- Attacker controls: the whole typedData object including domain and types
- Exploit idea: Issue two cross-app requests and cross the responses.
- Invariant to test: Cross-app responses must be correlated by an unguessable request id.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: cross two crossApp signTypedData: params [address responses and assert the mismatch is detected.
