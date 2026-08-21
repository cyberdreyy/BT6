# Q2765: captcha or rate-limit token optional client-side in SmartWalletApi.ts

## Question
Can an attacker omit or reuse the optional token/captchaToken argument on SmartWalletApi.init so the abuse control the app depends on is never carried on the request?

## Target
- File/function: [src/client/auth/SmartWalletApi.ts](src/client/auth/SmartWalletApi.ts) - SmartWalletApi.init, link, generateSmartWalletSiweMessage
- Entrypoint: privy.auth.smartWallet.init(wallet) then link(message, signature, type, version)
- Attacker controls: message override, signature, smart_wallet_type, smart_wallet_version, chainId
- Exploit idea: Call privy.auth.smartWallet.init(wallet) then link(message, signature, type, version) with the token argument undefined and observe the request still being sent.
- Invariant to test: src/client/auth/SmartWalletApi.ts must not send an authentication request whose required anti-abuse token is missing.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call SmartWalletApi.init without the token argument and assert the request is not issued.
