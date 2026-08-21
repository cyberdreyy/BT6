# Q2757: captcha or rate-limit token optional client-side in SiweApi.ts

## Question
Can an attacker omit or reuse the optional token/captchaToken argument on SiweApi.init so the abuse control the app depends on is never carried on the request?

## Target
- File/function: [src/client/auth/SiweApi.ts](src/client/auth/SiweApi.ts) - SiweApi.init, loginWithSiwe, linkWithSiwe, unlinkWallet, generateSiweMessage
- Entrypoint: privy.auth.siwe.init(wallet, domain, uri) then loginWithSiwe(signature, wallet, message)
- Attacker controls: domain, uri, chainId, walletClientType, connectorType, full message override, signature
- Exploit idea: Call privy.auth.siwe.init(wallet, domain, uri) then loginWithSiwe(signature, wallet, message) with the token argument undefined and observe the request still being sent.
- Invariant to test: src/client/auth/SiweApi.ts must not send an authentication request whose required anti-abuse token is missing.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call SiweApi.init without the token argument and assert the request is not issued.
