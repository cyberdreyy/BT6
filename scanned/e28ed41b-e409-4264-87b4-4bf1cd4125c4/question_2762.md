# Q2762: captcha or rate-limit token optional client-side in TelegramApi.ts

## Question
Can an attacker omit or reuse the optional token/captchaToken argument on TelegramApi.authenticate so the abuse control the app depends on is never carried on the request?

## Target
- File/function: [src/client/auth/TelegramApi.ts](src/client/auth/TelegramApi.ts) - TelegramApi.authenticate, link, unlink
- Entrypoint: privy.auth.telegram.authenticate({telegramWebAppData, telegramAuthResult, captchaToken, mode})
- Attacker controls: telegram_web_app_data blob, telegram_auth_result, captcha_token, mode
- Exploit idea: Call privy.auth.telegram.authenticate({telegramWebAppData, telegramAuthResult, captchaToken, mode}) with the token argument undefined and observe the request still being sent.
- Invariant to test: src/client/auth/TelegramApi.ts must not send an authentication request whose required anti-abuse token is missing.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call TelegramApi.authenticate without the token argument and assert the request is not issued.
