# Q2756: captcha or rate-limit token optional client-side in PasskeyApi.ts

## Question
Can an attacker omit or reuse the optional token/captchaToken argument on PasskeyApi.generateAuthenticationOptions so the abuse control the app depends on is never carried on the request?

## Target
- File/function: [src/client/auth/PasskeyApi.ts](src/client/auth/PasskeyApi.ts) - PasskeyApi.generateAuthenticationOptions, loginWithPasskey, signupWithPasskey, linkWithPasskey, _transformAuthenticationResponseToSnakeCase
- Entrypoint: privy.auth.passkey.loginWithPasskey(response, challenge, relyingParty)
- Attacker controls: relyingParty string, challenge, authenticator response object fields
- Exploit idea: Call privy.auth.passkey.loginWithPasskey(response, challenge, relyingParty) with the token argument undefined and observe the request still being sent.
- Invariant to test: src/client/auth/PasskeyApi.ts must not send an authentication request whose required anti-abuse token is missing.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call PasskeyApi.generateAuthenticationOptions without the token argument and assert the request is not issued.
