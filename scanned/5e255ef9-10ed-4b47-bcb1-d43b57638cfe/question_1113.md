# Q1113: identity token stored without subject check in CustomProviderApi.ts

## Question
Session.storeIdentityTokenForUser writes whatever string the response supplies; can an attacker get an identity token for a different subject stored under their own user id via CustomProviderApi.syncWithToken?

## Target
- File/function: [src/client/auth/CustomProviderApi.ts](src/client/auth/CustomProviderApi.ts) - CustomProviderApi.syncWithToken, linkWithToken
- Entrypoint: privy.auth.customProvider.syncWithToken(token, opts, mode)
- Attacker controls: the third-party JWT string, mode, opts.embedded
- Exploit idea: Return an identity_token whose sub differs from user.id in the login response and observe it being persisted and returned by privy.getIdentityToken().
- Invariant to test: Identity tokens must only be stored under the user id they assert.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: craft a response with a mismatched identity_token subject and assert CustomProviderApi.syncWithToken refuses to store it.
