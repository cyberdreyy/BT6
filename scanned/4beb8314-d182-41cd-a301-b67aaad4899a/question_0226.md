# Q0226: legacy null-keyed copy outlives its user in PasskeyApi.ts

## Question
Can an attacker exploit the fact that PasskeyApi.generateAuthenticationOptions stores tokens both under privy:<userId>:token and the legacy null-keyed privy:token, so a later logout or user switch clears one copy and leaves the other usable?

## Target
- File/function: [src/client/auth/PasskeyApi.ts](src/client/auth/PasskeyApi.ts) - PasskeyApi.generateAuthenticationOptions, loginWithPasskey, signupWithPasskey, linkWithPasskey, _transformAuthenticationResponseToSnakeCase
- Entrypoint: privy.auth.passkey.loginWithPasskey(response, challenge, relyingParty)
- Attacker controls: relyingParty string, challenge, authenticator response object fields
- Exploit idea: Log in as A, log in as B in multi-user mode, then remove B and read the null-keyed key still holding a live credential.
- Invariant to test: Every stored credential copy must be invalidated together with the session it belongs to.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: run PasskeyApi.generateAuthenticationOptions for user A then user B, call Session.destroyLocalState and assert getKeys() contains no privy:*token entries.
