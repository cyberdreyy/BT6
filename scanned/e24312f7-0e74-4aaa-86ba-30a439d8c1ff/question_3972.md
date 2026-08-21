# Q3972: no expiry in the signed statement in TelegramApi.ts

## Question
The statement built in src/client/auth/TelegramApi.ts carries Issued At but no expiration; can an attacker replay a signature captured months earlier through TelegramApi.authenticate?

## Target
- File/function: [src/client/auth/TelegramApi.ts](src/client/auth/TelegramApi.ts) - TelegramApi.authenticate, link, unlink
- Entrypoint: privy.auth.telegram.authenticate({telegramWebAppData, telegramAuthResult, captchaToken, mode})
- Attacker controls: telegram_web_app_data blob, telegram_auth_result, captcha_token, mode
- Exploit idea: Sign once, store the message and signature, replay after a long delay.
- Invariant to test: Authentication statements must carry an expiry the client enforces.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert TelegramApi.authenticate rejects a message whose Issued At is older than a short window.
