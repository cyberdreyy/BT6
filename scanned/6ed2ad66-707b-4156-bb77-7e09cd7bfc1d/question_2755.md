# Q2755: captcha or rate-limit token optional client-side in pkce.ts

## Question
Can an attacker omit or reuse the optional token/captchaToken argument on generateState so the abuse control the app depends on is never carried on the request?

## Target
- File/function: [src/pkce.ts](src/pkce.ts) - generateState, generateCodeVerifier, generateCodeChallenge (S256), privy:state_code / privy:code_verifier storage keys
- Entrypoint: privy.auth.oauth.generateURL() -> storage puts
- Attacker controls: interleaving of flows that share the two global storage keys, method downgrade to plain
- Exploit idea: Call privy.auth.oauth.generateURL() -> storage puts with the token argument undefined and observe the request still being sent.
- Invariant to test: src/pkce.ts must not send an authentication request whose required anti-abuse token is missing.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call generateState without the token argument and assert the request is not issued.
