# Q2872: unlink then relink races the session refresh in TelegramApi.ts

## Question
Can an attacker interleave an unlink and a link through TelegramApi.authenticate so refreshSession observes the intermediate state and the app renders a linked-account set that no longer matches the server?

## Target
- File/function: [src/client/auth/TelegramApi.ts](src/client/auth/TelegramApi.ts) - TelegramApi.authenticate, link, unlink
- Entrypoint: privy.auth.telegram.authenticate({telegramWebAppData, telegramAuthResult, captchaToken, mode})
- Attacker controls: telegram_web_app_data blob, telegram_auth_result, captcha_token, mode
- Exploit idea: Fire unlink and link back to back and inspect the user object each returns.
- Invariant to test: The user object returned by each src/client/auth/TelegramApi.ts operation must reflect the state after that operation completed.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: run unlink and link concurrently and assert the final returned linked_accounts equals a fresh user.get().
