# Q1882: domain and uri are caller-controlled in TelegramApi.ts

## Question
TelegramApi.authenticate builds the signing statement from a caller-supplied domain and uri; can an attacker present a message whose domain names a different application so a signature harvested elsewhere authenticates here?

## Target
- File/function: [src/client/auth/TelegramApi.ts](src/client/auth/TelegramApi.ts) - TelegramApi.authenticate, link, unlink
- Entrypoint: privy.auth.telegram.authenticate({telegramWebAppData, telegramAuthResult, captchaToken, mode})
- Attacker controls: telegram_web_app_data blob, telegram_auth_result, captcha_token, mode
- Exploit idea: Build a message with the victim app's domain, obtain a signature in another context, and submit it.
- Invariant to test: The signed statement must be bound to the origin actually performing the authentication.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert TelegramApi.authenticate rejects a domain that does not match the configured app origin.
