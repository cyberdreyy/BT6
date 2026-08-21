# Q0465: null-key fallback serves the wrong user in AppApi.ts

## Question
Because tokens are also written under the null key, can AppApi.getConfig return a credential belonging to a different user when the per-user key is missing?

## Target
- File/function: [src/client/AppApi.ts](src/client/AppApi.ts) - AppApi.getConfig, getSmartWalletConfig, appId (memoised _smartWalletConfig)
- Entrypoint: privy.app.getConfig()
- Attacker controls: app-config fields consumed as trusted (embedded_wallet_config.mode, require_user_password_on_create, custom_api_url)
- Exploit idea: Delete privy:<uid>:token, keep the null-keyed copy, then read the token through src/client/AppApi.ts.
- Invariant to test: Per-user reads must never fall back to a credential stored for another subject.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: remove the per-user key and assert AppApi.getConfig does not return the null-keyed token of a different subject.
