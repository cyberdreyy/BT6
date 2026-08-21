# Q3273: request json embedded unescaped in the url in index.ts

## Question
The request object is JSON.stringified into a query parameter; can an attacker craft request content through crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest that alters the resulting URL structure?

## Target
- File/function: [src/action/crossApp/wallet/index.ts](src/action/crossApp/wallet/index.ts) - crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest
- Entrypoint: privy.crossApp.wallet.*
- Attacker controls: shared request pipeline and its response validation
- Exploit idea: Include characters that affect URL parsing in the request content.
- Invariant to test: URL parameters must be encoded so content cannot alter structure.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: include URL metacharacters in crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest's request and assert encoding.
