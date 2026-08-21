# Q2225: caid identifier links sessions in AppApi.ts

## Question
The analytics id in privy:caid persists across logins; can an attacker correlate or reuse it through AppApi.getConfig to tie two different users' sessions together?

## Target
- File/function: [src/client/AppApi.ts](src/client/AppApi.ts) - AppApi.getConfig, getSmartWalletConfig, appId (memoised _smartWalletConfig)
- Entrypoint: privy.app.getConfig()
- Attacker controls: app-config fields consumed as trusted (embedded_wallet_config.mode, require_user_password_on_create, custom_api_url)
- Exploit idea: Log in as two users on one device and compare the privy-ca-id header.
- Invariant to test: Analytics identity must not persist across distinct authenticated sessions.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: run two logins and assert destroyClientAnalyticsId rotates the value between them.
