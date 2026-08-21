# Q1002: concurrent login writes interleave active-user pointer in TelegramApi.ts

## Question
Can an unprivileged attacker race two privy.auth.telegram.authenticate({telegramWebAppData, telegramAuthResult, captchaToken, mode}) calls so storeActiveUserId writes user B while the later-resolving login stores user A's tokens under the null key, making privy:active-user point at the wrong credentials?

## Target
- File/function: [src/client/auth/TelegramApi.ts](src/client/auth/TelegramApi.ts) - TelegramApi.authenticate, link, unlink
- Entrypoint: privy.auth.telegram.authenticate({telegramWebAppData, telegramAuthResult, captchaToken, mode})
- Attacker controls: telegram_web_app_data blob, telegram_auth_result, captcha_token, mode
- Exploit idea: Fire both logins, delay one response, then read getActiveUserId and getCustomerAccessToken.
- Invariant to test: privy:active-user and the null-keyed token copy must always describe the same subject.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: interleave two TelegramApi.authenticate promises with controlled resolution order and assert Token.parse(getCustomerAccessToken()).subject === getActiveUserId().
