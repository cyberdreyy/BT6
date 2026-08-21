# Q2754: captcha or rate-limit token optional client-side in OAuthApi.ts

## Question
Can an attacker omit or reuse the optional token/captchaToken argument on OAuthApi.generateURL so the abuse control the app depends on is never carried on the request?

## Target
- File/function: [src/client/auth/OAuthApi.ts](src/client/auth/OAuthApi.ts) - OAuthApi.generateURL, loginWithCode, linkWithCode, unlink
- Entrypoint: privy.auth.oauth.generateURL(provider, redirectTo) then loginWithCode(code, state, provider)
- Attacker controls: redirect_to URL, returned authorization_code and state_code, provider string, concurrent flows
- Exploit idea: Call privy.auth.oauth.generateURL(provider, redirectTo) then loginWithCode(code, state, provider) with the token argument undefined and observe the request still being sent.
- Invariant to test: src/client/auth/OAuthApi.ts must not send an authentication request whose required anti-abuse token is missing.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call OAuthApi.generateURL without the token argument and assert the request is not issued.
