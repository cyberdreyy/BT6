# Q3264: request json embedded unescaped in the url in getProviderAccessTokenOrRelink.ts

## Question
The request object is JSON.stringified into a query parameter; can an attacker craft request content through getProviderAccessTokenOrRelink: cached token from storage else relink that alters the resulting URL structure?

## Target
- File/function: [src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts](src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts) - getProviderAccessTokenOrRelink: cached token from storage else relink
- Entrypoint: cross-app wallet operations
- Attacker controls: the cached privy:cross-app:<appId> value and its decoded expiry
- Exploit idea: Include characters that affect URL parsing in the request content.
- Invariant to test: URL parameters must be encoded so content cannot alter structure.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: include URL metacharacters in getProviderAccessTokenOrRelink: cached token from storage else relink's request and assert encoding.
