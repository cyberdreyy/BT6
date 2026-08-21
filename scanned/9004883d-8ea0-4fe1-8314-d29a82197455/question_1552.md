# Q1552: code_verifier survives a failed exchange in TelegramApi.ts

## Question
Does TelegramApi.authenticate leave privy:code_verifier and privy:state_code in storage when the exchange throws, so a later attacker-triggered callback can replay them?

## Target
- File/function: [src/client/auth/TelegramApi.ts](src/client/auth/TelegramApi.ts) - TelegramApi.authenticate, link, unlink
- Entrypoint: privy.auth.telegram.authenticate({telegramWebAppData, telegramAuthResult, captchaToken, mode})
- Attacker controls: telegram_web_app_data blob, telegram_auth_result, captcha_token, mode
- Exploit idea: Fail the authenticate request, then deliver a crafted callback that reuses the still-stored state/verifier pair.
- Invariant to test: PKCE material must be deleted on every terminal outcome, not only on success.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: make the exchange reject and assert both storage keys are absent afterwards.
