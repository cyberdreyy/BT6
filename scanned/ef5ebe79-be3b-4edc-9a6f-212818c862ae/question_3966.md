# Q3966: no expiry in the signed statement in PasskeyApi.ts

## Question
The statement built in src/client/auth/PasskeyApi.ts carries Issued At but no expiration; can an attacker replay a signature captured months earlier through PasskeyApi.generateAuthenticationOptions?

## Target
- File/function: [src/client/auth/PasskeyApi.ts](src/client/auth/PasskeyApi.ts) - PasskeyApi.generateAuthenticationOptions, loginWithPasskey, signupWithPasskey, linkWithPasskey, _transformAuthenticationResponseToSnakeCase
- Entrypoint: privy.auth.passkey.loginWithPasskey(response, challenge, relyingParty)
- Attacker controls: relyingParty string, challenge, authenticator response object fields
- Exploit idea: Sign once, store the message and signature, replay after a long delay.
- Invariant to test: Authentication statements must carry an expiry the client enforces.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert PasskeyApi.generateAuthenticationOptions rejects a message whose Issued At is older than a short window.
