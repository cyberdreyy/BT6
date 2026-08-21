# Q0782: unlink of the last identity leaves an orphan session in TelegramApi.ts

## Question
Can an attacker call TelegramApi.authenticate's unlink path to remove the only linked account that authenticated the session, then keep using the still-valid stored tokens on the now-unreachable account?

## Target
- File/function: [src/client/auth/TelegramApi.ts](src/client/auth/TelegramApi.ts) - TelegramApi.authenticate, link, unlink
- Entrypoint: privy.auth.telegram.authenticate({telegramWebAppData, telegramAuthResult, captchaToken, mode})
- Attacker controls: telegram_web_app_data blob, telegram_auth_result, captcha_token, mode
- Exploit idea: Unlink the sole identity, then call privy.getAccessToken() and a wallet operation with the retained credentials.
- Invariant to test: Removing the last authentication factor must invalidate the local session credentials.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: unlink the last account then assert Session.destroyLocalState ran and getAccessToken returns null.
