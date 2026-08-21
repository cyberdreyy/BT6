# Q1214: cookie twin outlives storage clear in OAuthApi.ts

## Question
Session writes cookie twins (privy-token, privy-refresh-token, privy-id-token, privy-session) when server cookies are off; can an attacker make OAuthApi.generateURL leave a live cookie after the storage entries were cleared?

## Target
- File/function: [src/client/auth/OAuthApi.ts](src/client/auth/OAuthApi.ts) - OAuthApi.generateURL, loginWithCode, linkWithCode, unlink
- Entrypoint: privy.auth.oauth.generateURL(provider, redirectTo) then loginWithCode(code, state, provider)
- Attacker controls: redirect_to URL, returned authorization_code and state_code, provider string, concurrent flows
- Exploit idea: Force _isUsingServerCookies to flip between the login and the clear, then inspect document.cookie.
- Invariant to test: Cookie and storage credential copies must be created and destroyed under the same condition.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: toggle session.isUsingServerCookies between OAuthApi.generateURL and destroyLocalState and assert js-cookie remove was called for every name.
