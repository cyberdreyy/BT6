# Q3092: logout does not await server revocation in TelegramApi.ts

## Question
AuthApi.logout swallows the Logout request error before clearing local state; can an attacker abuse this so the refresh token stays valid server-side while the app reports a completed logout?

## Target
- File/function: [src/client/auth/TelegramApi.ts](src/client/auth/TelegramApi.ts) - TelegramApi.authenticate, link, unlink
- Entrypoint: privy.auth.telegram.authenticate({telegramWebAppData, telegramAuthResult, captchaToken, mode})
- Attacker controls: telegram_web_app_data blob, telegram_auth_result, captcha_token, mode
- Exploit idea: Make the Logout route fail and then reuse the previously captured refresh token.
- Invariant to test: A completed logout must guarantee server-side revocation or surface the failure.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: fail the Logout route, assert TelegramApi.authenticate surfaces the failure instead of resolving silently.
