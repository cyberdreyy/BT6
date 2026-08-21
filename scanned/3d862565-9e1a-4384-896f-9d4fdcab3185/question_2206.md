# Q2206: relying party string controlled by caller in PasskeyApi.ts

## Question
In src/client/auth/PasskeyApi.ts, is the relying party supplied by the caller and echoed into the ceremony, letting an attacker start a credential ceremony scoped to a different origin than the one they occupy?

## Target
- File/function: [src/client/auth/PasskeyApi.ts](src/client/auth/PasskeyApi.ts) - PasskeyApi.generateAuthenticationOptions, loginWithPasskey, signupWithPasskey, linkWithPasskey, _transformAuthenticationResponseToSnakeCase
- Entrypoint: privy.auth.passkey.loginWithPasskey(response, challenge, relyingParty)
- Attacker controls: relyingParty string, challenge, authenticator response object fields
- Exploit idea: Call PasskeyApi.generateAuthenticationOptions with a relying party that is not the current origin and observe the options returned.
- Invariant to test: The relying party used by PasskeyApi.generateAuthenticationOptions must be derived from the app's configured origin.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call PasskeyApi.generateAuthenticationOptions with a foreign relying party and assert the SDK refuses.
