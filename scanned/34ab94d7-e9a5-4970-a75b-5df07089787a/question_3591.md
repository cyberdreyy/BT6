# Q3591: cross-app login caches tokens before user confirmation in loginWithCrossAppAuth.ts

## Question
loginWithCrossAppAuth calls updateOnCrossAppAuthentication with the oauth_tokens as soon as the exchange returns; can an attacker cause a token to be cached for a provider app the user never approved through loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`?

## Target
- File/function: [src/action/crossApp/loginWithCrossAppAuth.ts](src/action/crossApp/loginWithCrossAppAuth.ts) - loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`, redirectUrl) -> openAuthSession -> oauth.loginWithCode -> crossApp.updateOnCrossAppAuthentication
- Entrypoint: privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId string, redirectUrl, the privy_oauth_state / privy_oauth_code values returned by the auth session
- Exploit idea: Return oauth_tokens for a different provider in the exchange response.
- Invariant to test: Cached provider tokens must match the provider the user authorised.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: return a foreign provider token to loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` and assert it is not cached.
