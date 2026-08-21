# Q2763: captcha or rate-limit token optional client-side in CustomProviderApi.ts

## Question
Can an attacker omit or reuse the optional token/captchaToken argument on CustomProviderApi.syncWithToken so the abuse control the app depends on is never carried on the request?

## Target
- File/function: [src/client/auth/CustomProviderApi.ts](src/client/auth/CustomProviderApi.ts) - CustomProviderApi.syncWithToken, linkWithToken
- Entrypoint: privy.auth.customProvider.syncWithToken(token, opts, mode)
- Attacker controls: the third-party JWT string, mode, opts.embedded
- Exploit idea: Call privy.auth.customProvider.syncWithToken(token, opts, mode) with the token argument undefined and observe the request still being sent.
- Invariant to test: src/client/auth/CustomProviderApi.ts must not send an authentication request whose required anti-abuse token is missing.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call CustomProviderApi.syncWithToken without the token argument and assert the request is not issued.
