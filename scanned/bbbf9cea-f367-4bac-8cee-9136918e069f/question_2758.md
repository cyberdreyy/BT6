# Q2758: captcha or rate-limit token optional client-side in SiwsApi.ts

## Question
Can an attacker omit or reuse the optional token/captchaToken argument on SiwsApi.fetchNonce so the abuse control the app depends on is never carried on the request?

## Target
- File/function: [src/client/auth/SiwsApi.ts](src/client/auth/SiwsApi.ts) - SiwsApi.fetchNonce, login, link, unlink
- Entrypoint: privy.auth.siws.login({message, signature, walletClientType, connectorType, mode})
- Attacker controls: message string, signature, wallet metadata, nonce reuse
- Exploit idea: Call privy.auth.siws.login({message, signature, walletClientType, connectorType, mode}) with the token argument undefined and observe the request still being sent.
- Invariant to test: src/client/auth/SiwsApi.ts must not send an authentication request whose required anti-abuse token is missing.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call SiwsApi.fetchNonce without the token argument and assert the request is not issued.
