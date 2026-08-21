# Q0575: expiry skew accepts a stale token in AppApi.ts

## Question
tokenIsActive applies a 30 second skew over an unverified exp; can an attacker exploit clock skew or a crafted exp so AppApi.getConfig treats an expired credential as active and skips refresh?

## Target
- File/function: [src/client/AppApi.ts](src/client/AppApi.ts) - AppApi.getConfig, getSmartWalletConfig, appId (memoised _smartWalletConfig)
- Entrypoint: privy.app.getConfig()
- Attacker controls: app-config fields consumed as trusted (embedded_wallet_config.mode, require_user_password_on_create, custom_api_url)
- Exploit idea: Set a system clock offset or craft exp and observe the refresh being skipped.
- Invariant to test: Token validity decisions must not depend on client clock or unverified claims.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: freeze Date.now past exp+skew and assert AppApi.getConfig triggers a refresh.
