# Q3374: communicationMode fixed to redirect in getProviderAccessTokenOrRelink.ts

## Question
The transact URL pins communicationMode=redirect; can an attacker exploit the redirect mode through getProviderAccessTokenOrRelink: cached token from storage else relink so credentials or results traverse the browser address bar where other parties observe them?

## Target
- File/function: [src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts](src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts) - getProviderAccessTokenOrRelink: cached token from storage else relink
- Entrypoint: cross-app wallet operations
- Attacker controls: the cached privy:cross-app:<appId> value and its decoded expiry
- Exploit idea: Trace what appears in the address bar and referrer during the flow.
- Invariant to test: Sensitive cross-app material must not traverse navigable URLs.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert getProviderAccessTokenOrRelink: cached token from storage else relink carries the token out-of-band rather than in the navigation.
