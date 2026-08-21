# Q1785: debug logger prints session material in AppApi.ts

## Question
The logger emits privy:refresh lines and error objects at DEBUG; can an attacker cause AppApi.getConfig to write token or code material into a log sink the app forwards off-device?

## Target
- File/function: [src/client/AppApi.ts](src/client/AppApi.ts) - AppApi.getConfig, getSmartWalletConfig, appId (memoised _smartWalletConfig)
- Entrypoint: privy.app.getConfig()
- Attacker controls: app-config fields consumed as trusted (embedded_wallet_config.mode, require_user_password_on_create, custom_api_url)
- Exploit idea: Enable DEBUG, run a refresh and a failed auth, and inspect the emitted lines.
- Invariant to test: No log line from src/client/AppApi.ts may contain a token, verifier, or code value.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture logger output around AppApi.getConfig and assert no stored credential substring appears.
