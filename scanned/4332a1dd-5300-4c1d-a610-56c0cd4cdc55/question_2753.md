# Q2753: captcha or rate-limit token optional client-side in PhoneApi.ts

## Question
Can an attacker omit or reuse the optional token/captchaToken argument on PhoneApi.sendCode so the abuse control the app depends on is never carried on the request?

## Target
- File/function: [src/client/auth/PhoneApi.ts](src/client/auth/PhoneApi.ts) - PhoneApi.sendCode, loginWithCode, linkWithCode, updatePhone, unlink
- Entrypoint: privy.auth.phone.loginWithCode(phone, code)
- Attacker controls: phoneNumber string (unnormalized), code, mode, opts.embedded
- Exploit idea: Call privy.auth.phone.loginWithCode(phone, code) with the token argument undefined and observe the request still being sent.
- Invariant to test: src/client/auth/PhoneApi.ts must not send an authentication request whose required anti-abuse token is missing.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call PhoneApi.sendCode without the token argument and assert the request is not issued.
