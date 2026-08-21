# Q3601: cross-app login caches tokens before user confirmation in CrossAppApi.ts

## Question
loginWithCrossAppAuth calls updateOnCrossAppAuthentication with the oauth_tokens as soon as the exchange returns; can an attacker cause a token to be cached for a provider app the user never approved through CrossAppApi.updateOnCrossAppAuthentication?

## Target
- File/function: [src/client/CrossAppApi.ts](src/client/CrossAppApi.ts) - CrossAppApi.updateOnCrossAppAuthentication, getProviderAccessToken (Token expiry only), getCrossAppConnections, providerAccessTokenStorageKey('privy:cross-app:<appId>')
- Entrypoint: privy.crossApp.getProviderAccessToken(appId)
- Attacker controls: the stored provider access token string and the provider app id used to key it
- Exploit idea: Return oauth_tokens for a different provider in the exchange response.
- Invariant to test: Cached provider tokens must match the provider the user authorised.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: return a foreign provider token to CrossAppApi.updateOnCrossAppAuthentication and assert it is not cached.
