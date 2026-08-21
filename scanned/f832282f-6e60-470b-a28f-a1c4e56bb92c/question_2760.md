# Q2760: captcha or rate-limit token optional client-side in FarcasterApi.ts

## Question
Can an attacker omit or reuse the optional token/captchaToken argument on FarcasterApi.initializeAuth so the abuse control the app depends on is never carried on the request?

## Target
- File/function: [src/client/auth/FarcasterApi.ts](src/client/auth/FarcasterApi.ts) - FarcasterApi.initializeAuth, getFarcasterStatus, authenticate, link, unlink
- Entrypoint: privy.auth.farcaster.authenticate({channel_token, message, signature, fid})
- Attacker controls: channel_token header value, message, signature, fid, relying_party, redirect_url
- Exploit idea: Call privy.auth.farcaster.authenticate({channel_token, message, signature, fid}) with the token argument undefined and observe the request still being sent.
- Invariant to test: src/client/auth/FarcasterApi.ts must not send an authentication request whose required anti-abuse token is missing.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call FarcasterApi.initializeAuth without the token argument and assert the request is not issued.
