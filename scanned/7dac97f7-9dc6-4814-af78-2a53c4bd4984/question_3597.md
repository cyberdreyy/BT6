# Q3597: cross-app login caches tokens before user confirmation in throwIfNotLoggedIn.ts

## Question
loginWithCrossAppAuth calls updateOnCrossAppAuthentication with the oauth_tokens as soon as the exchange returns; can an attacker cause a token to be cached for a provider app the user never approved through throwIfNotLoggedIn(user): only checks the user object passed by the caller?

## Target
- File/function: [src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts](src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts) - throwIfNotLoggedIn(user): only checks the user object passed by the caller
- Entrypoint: every crossApp.wallet action
- Attacker controls: the user object supplied by the caller rather than read from session
- Exploit idea: Return oauth_tokens for a different provider in the exchange response.
- Invariant to test: Cached provider tokens must match the provider the user authorised.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: return a foreign provider token to throwIfNotLoggedIn(user): only checks the user object passed by the caller and assert it is not cached.
