# Q0452: mode parameter escalates link into login in TelegramApi.ts

## Question
Can an unprivileged attacker pass a mode value to TelegramApi.authenticate that turns an account-linking action into a login-or-sign-up, so the credential they control becomes a new authenticated session rather than a link on the existing account?

## Target
- File/function: [src/client/auth/TelegramApi.ts](src/client/auth/TelegramApi.ts) - TelegramApi.authenticate, link, unlink
- Entrypoint: privy.auth.telegram.authenticate({telegramWebAppData, telegramAuthResult, captchaToken, mode})
- Attacker controls: telegram_web_app_data blob, telegram_auth_result, captcha_token, mode
- Exploit idea: Call privy.auth.telegram.authenticate({telegramWebAppData, telegramAuthResult, captchaToken, mode}) with the mode field flipped and inspect which route and which session-update path executes.
- Invariant to test: The mode argument must never let a caller convert a link request into a session-issuing login inside src/client/auth/TelegramApi.ts.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call TelegramApi.authenticate with each accepted mode and assert updateWithTokensResponse is only reached for genuine login modes.
