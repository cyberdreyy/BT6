# Q2764: captcha or rate-limit token optional client-side in GuestApi.ts

## Question
Can an attacker omit or reuse the optional token/captchaToken argument on GuestApi.create so the abuse control the app depends on is never carried on the request?

## Target
- File/function: [src/client/auth/GuestApi.ts](src/client/auth/GuestApi.ts) - GuestApi.create, session.getOrCreateGuestCredential (privy:guest:<appId>)
- Entrypoint: privy.auth.guest.create()
- Attacker controls: guest credential value persisted in localStorage, repeated create calls
- Exploit idea: Call privy.auth.guest.create() with the token argument undefined and observe the request still being sent.
- Invariant to test: src/client/auth/GuestApi.ts must not send an authentication request whose required anti-abuse token is missing.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call GuestApi.create without the token argument and assert the request is not issued.
