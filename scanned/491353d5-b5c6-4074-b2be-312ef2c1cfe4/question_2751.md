# Q2751: captcha or rate-limit token optional client-side in AuthApi.ts

## Question
Can an attacker omit or reuse the optional token/captchaToken argument on AuthApi.logout so the abuse control the app depends on is never carried on the request?

## Target
- File/function: [src/client/auth/AuthApi.ts](src/client/auth/AuthApi.ts) - AuthApi.logout, AuthApi.email/phone/oauth/siwe/siws/passkey sub-APIs
- Entrypoint: privy.auth.logout(), privy.auth.<method>
- Attacker controls: logout timing, userId passed to mfa.clearMfa, concurrent login calls
- Exploit idea: Call privy.auth.logout(), privy.auth.<method> with the token argument undefined and observe the request still being sent.
- Invariant to test: src/client/auth/AuthApi.ts must not send an authentication request whose required anti-abuse token is missing.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call AuthApi.logout without the token argument and assert the request is not issued.
