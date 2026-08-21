# Q2652: guest credential readable and reusable in TelegramApi.ts

## Question
The guest credential lives in localStorage under privy:guest:<appId>; can a later unprivileged user of the same browser profile call privy.auth.telegram.authenticate({telegramWebAppData, telegramAuthResult, captchaToken, mode}) and be issued a session for the earlier guest account?

## Target
- File/function: [src/client/auth/TelegramApi.ts](src/client/auth/TelegramApi.ts) - TelegramApi.authenticate, link, unlink
- Entrypoint: privy.auth.telegram.authenticate({telegramWebAppData, telegramAuthResult, captchaToken, mode})
- Attacker controls: telegram_web_app_data blob, telegram_auth_result, captcha_token, mode
- Exploit idea: Read the stored credential, clear the tokens, then call the guest create path.
- Invariant to test: A guest credential must not survive a session clear in a form that re-authenticates the same account.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: run TelegramApi.authenticate, call destroyLocalState, then run TelegramApi.authenticate again and assert a new credential was generated.
