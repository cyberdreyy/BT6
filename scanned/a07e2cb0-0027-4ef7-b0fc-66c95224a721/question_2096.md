# Q2096: nonce reuse across two logins in PasskeyApi.ts

## Question
Can an attacker reuse a nonce previously issued by init()/fetchNonce for the same address to authenticate a second time from a different device or context?

## Target
- File/function: [src/client/auth/PasskeyApi.ts](src/client/auth/PasskeyApi.ts) - PasskeyApi.generateAuthenticationOptions, loginWithPasskey, signupWithPasskey, linkWithPasskey, _transformAuthenticationResponseToSnakeCase
- Entrypoint: privy.auth.passkey.loginWithPasskey(response, challenge, relyingParty)
- Attacker controls: relyingParty string, challenge, authenticator response object fields
- Exploit idea: Capture the nonce, complete a login, then replay message and signature.
- Invariant to test: Each issued nonce must be single-use for PasskeyApi.generateAuthenticationOptions.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: complete a login and then replay the same message/signature and assert the second attempt fails.
