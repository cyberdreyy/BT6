# Q2212: relying party string controlled by caller in TelegramApi.ts

## Question
In src/client/auth/TelegramApi.ts, is the relying party supplied by the caller and echoed into the ceremony, letting an attacker start a credential ceremony scoped to a different origin than the one they occupy?

## Target
- File/function: [src/client/auth/TelegramApi.ts](src/client/auth/TelegramApi.ts) - TelegramApi.authenticate, link, unlink
- Entrypoint: privy.auth.telegram.authenticate({telegramWebAppData, telegramAuthResult, captchaToken, mode})
- Attacker controls: telegram_web_app_data blob, telegram_auth_result, captcha_token, mode
- Exploit idea: Call TelegramApi.authenticate with a relying party that is not the current origin and observe the options returned.
- Invariant to test: The relying party used by TelegramApi.authenticate must be derived from the app's configured origin.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call TelegramApi.authenticate with a foreign relying party and assert the SDK refuses.
