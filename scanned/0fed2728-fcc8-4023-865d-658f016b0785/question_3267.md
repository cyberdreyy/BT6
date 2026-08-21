# Q3267: request json embedded unescaped in the url in throwIfNotLoggedIn.ts

## Question
The request object is JSON.stringified into a query parameter; can an attacker craft request content through throwIfNotLoggedIn(user): only checks the user object passed by the caller that alters the resulting URL structure?

## Target
- File/function: [src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts](src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts) - throwIfNotLoggedIn(user): only checks the user object passed by the caller
- Entrypoint: every crossApp.wallet action
- Attacker controls: the user object supplied by the caller rather than read from session
- Exploit idea: Include characters that affect URL parsing in the request content.
- Invariant to test: URL parameters must be encoded so content cannot alter structure.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: include URL metacharacters in throwIfNotLoggedIn(user): only checks the user object passed by the caller's request and assert encoding.
