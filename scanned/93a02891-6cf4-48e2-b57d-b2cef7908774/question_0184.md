# Q0184: provider api url comes from the connections list in getProviderAccessTokenOrRelink.ts

## Question
The transact URL host is provider_app_custom_api_url taken from the getCrossAppConnections response; can an attacker influence that value so getProviderAccessTokenOrRelink: cached token from storage else relink sends the provider access token and the request payload to a host of their choosing?

## Target
- File/function: [src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts](src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts) - getProviderAccessTokenOrRelink: cached token from storage else relink
- Entrypoint: cross-app wallet operations
- Attacker controls: the cached privy:cross-app:<appId> value and its decoded expiry
- Exploit idea: Return a connections entry with an attacker host and observe the outbound request.
- Invariant to test: Cross-app endpoints must be validated against a trusted registry before credentials are attached.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: return a crafted provider_app_custom_api_url and assert getProviderAccessTokenOrRelink: cached token from storage else relink refuses to send the token.
