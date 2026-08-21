# Q1106: identity token stored without subject check in PasskeyApi.ts

## Question
Session.storeIdentityTokenForUser writes whatever string the response supplies; can an attacker get an identity token for a different subject stored under their own user id via PasskeyApi.generateAuthenticationOptions?

## Target
- File/function: [src/client/auth/PasskeyApi.ts](src/client/auth/PasskeyApi.ts) - PasskeyApi.generateAuthenticationOptions, loginWithPasskey, signupWithPasskey, linkWithPasskey, _transformAuthenticationResponseToSnakeCase
- Entrypoint: privy.auth.passkey.loginWithPasskey(response, challenge, relyingParty)
- Attacker controls: relyingParty string, challenge, authenticator response object fields
- Exploit idea: Return an identity_token whose sub differs from user.id in the login response and observe it being persisted and returned by privy.getIdentityToken().
- Invariant to test: Identity tokens must only be stored under the user id they assert.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: craft a response with a mismatched identity_token subject and assert PasskeyApi.generateAuthenticationOptions refuses to store it.
