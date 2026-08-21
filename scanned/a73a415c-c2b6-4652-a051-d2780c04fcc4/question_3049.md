# Q3049: connections list fetched per request in signTypedData.ts

## Question
getCrossAppConnections is fetched on each wallet action; can an attacker cause the list to change between the resolution and the request in crossApp signTypedData: params [address so the token is sent to a different provider than the one authorised?

## Target
- File/function: [src/action/crossApp/wallet/signTypedData.ts](src/action/crossApp/wallet/signTypedData.ts) - crossApp signTypedData: params [address, generateDomainType(typedData)]
- Entrypoint: privy.crossApp.wallet.signTypedData({user, typedData, address, redirectUrl})
- Attacker controls: the whole typedData object including domain and types
- Exploit idea: Change the connections response between the two awaits.
- Invariant to test: Provider identity must be pinned for the duration of an operation.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Integration test: swap the connections mid-call in crossApp signTypedData: params [address and assert abort.
