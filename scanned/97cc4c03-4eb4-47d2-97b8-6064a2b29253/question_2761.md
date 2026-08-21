# Q2761: captcha or rate-limit token optional client-side in FarcasterV2Api.ts

## Question
Can an attacker omit or reuse the optional token/captchaToken argument on FarcasterV2Api.initializeAuth so the abuse control the app depends on is never carried on the request?

## Target
- File/function: [src/client/auth/FarcasterV2Api.ts](src/client/auth/FarcasterV2Api.ts) - FarcasterV2Api.initializeAuth, authenticate
- Entrypoint: privy.auth.farcasterV2.authenticate({message, signature, fid})
- Attacker controls: SIWF message, signature, fid
- Exploit idea: Call privy.auth.farcasterV2.authenticate({message, signature, fid}) with the token argument undefined and observe the request still being sent.
- Invariant to test: src/client/auth/FarcasterV2Api.ts must not send an authentication request whose required anti-abuse token is missing.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call FarcasterV2Api.initializeAuth without the token argument and assert the request is not issued.
