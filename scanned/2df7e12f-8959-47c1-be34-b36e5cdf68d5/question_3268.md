# Q3268: request json embedded unescaped in the url in signMessage.ts

## Question
The request object is JSON.stringified into a query parameter; can an attacker craft request content through crossApp signMessage: params [message that alters the resulting URL structure?

## Target
- File/function: [src/action/crossApp/wallet/signMessage.ts](src/action/crossApp/wallet/signMessage.ts) - crossApp signMessage: params [message, address], method chosen by isCrossAppWalletSmart
- Entrypoint: privy.crossApp.wallet.signMessage({user, address, message, redirectUrl})
- Attacker controls: message bytes/string, address, redirectUrl, provider response payload
- Exploit idea: Include characters that affect URL parsing in the request content.
- Invariant to test: URL parameters must be encoded so content cannot alter structure.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: include URL metacharacters in crossApp signMessage: params [message's request and assert encoding.
