# Q1105: identity token stored without subject check in pkce.ts

## Question
Session.storeIdentityTokenForUser writes whatever string the response supplies; can an attacker get an identity token for a different subject stored under their own user id via generateState?

## Target
- File/function: [src/pkce.ts](src/pkce.ts) - generateState, generateCodeVerifier, generateCodeChallenge (S256), privy:state_code / privy:code_verifier storage keys
- Entrypoint: privy.auth.oauth.generateURL() -> storage puts
- Attacker controls: interleaving of flows that share the two global storage keys, method downgrade to plain
- Exploit idea: Return an identity_token whose sub differs from user.id in the login response and observe it being persisted and returned by privy.getIdentityToken().
- Invariant to test: Identity tokens must only be stored under the user id they assert.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: craft a response with a mismatched identity_token subject and assert generateState refuses to store it.
