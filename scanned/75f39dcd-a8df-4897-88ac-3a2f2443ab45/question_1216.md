# Q1216: cookie twin outlives storage clear in PasskeyApi.ts

## Question
Session writes cookie twins (privy-token, privy-refresh-token, privy-id-token, privy-session) when server cookies are off; can an attacker make PasskeyApi.generateAuthenticationOptions leave a live cookie after the storage entries were cleared?

## Target
- File/function: [src/client/auth/PasskeyApi.ts](src/client/auth/PasskeyApi.ts) - PasskeyApi.generateAuthenticationOptions, loginWithPasskey, signupWithPasskey, linkWithPasskey, _transformAuthenticationResponseToSnakeCase
- Entrypoint: privy.auth.passkey.loginWithPasskey(response, challenge, relyingParty)
- Attacker controls: relyingParty string, challenge, authenticator response object fields
- Exploit idea: Force _isUsingServerCookies to flip between the login and the clear, then inspect document.cookie.
- Invariant to test: Cookie and storage credential copies must be created and destroyed under the same condition.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: toggle session.isUsingServerCookies between PasskeyApi.generateAuthenticationOptions and destroyLocalState and assert js-cookie remove was called for every name.
