# Q2752: captcha or rate-limit token optional client-side in EmailApi.ts

## Question
Can an attacker omit or reuse the optional token/captchaToken argument on EmailApi.sendCode so the abuse control the app depends on is never carried on the request?

## Target
- File/function: [src/client/auth/EmailApi.ts](src/client/auth/EmailApi.ts) - EmailApi.sendCode, loginWithCode, linkWithCode, updateEmail, unlink
- Entrypoint: privy.auth.email.loginWithCode(email, code)
- Attacker controls: email string, code string, mode, opts.embedded, call ordering/repetition
- Exploit idea: Call privy.auth.email.loginWithCode(email, code) with the token argument undefined and observe the request still being sent.
- Invariant to test: src/client/auth/EmailApi.ts must not send an authentication request whose required anti-abuse token is missing.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call EmailApi.sendCode without the token argument and assert the request is not issued.
