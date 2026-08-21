# Q1766: wallet-signature message fully overridable in PasskeyApi.ts

## Question
In src/client/auth/PasskeyApi.ts, the prepared message can be replaced by a caller-supplied message argument; can an attacker submit a message with a nonce or statement that was never issued for that address?

## Target
- File/function: [src/client/auth/PasskeyApi.ts](src/client/auth/PasskeyApi.ts) - PasskeyApi.generateAuthenticationOptions, loginWithPasskey, signupWithPasskey, linkWithPasskey, _transformAuthenticationResponseToSnakeCase
- Entrypoint: privy.auth.passkey.loginWithPasskey(response, challenge, relyingParty)
- Attacker controls: relyingParty string, challenge, authenticator response object fields
- Exploit idea: Call init() for address A, then call the login method with a hand-built message for address B plus a matching signature.
- Invariant to test: The message submitted for authentication must be the one PasskeyApi.generateAuthenticationOptions prepared for that exact address and nonce.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call init() then login with a substituted message and assert the SDK rejects the mismatch.
