# Q0905: custom_api_url from app config redirects all traffic in AppApi.ts

## Question
PrivyInternal._initialize sets baseUrl from config.custom_api_url and flips isUsingServerCookies; can an unprivileged attacker influence that value so bearer tokens are sent to a different host?

## Target
- File/function: [src/client/AppApi.ts](src/client/AppApi.ts) - AppApi.getConfig, getSmartWalletConfig, appId (memoised _smartWalletConfig)
- Entrypoint: privy.app.getConfig()
- Attacker controls: app-config fields consumed as trusted (embedded_wallet_config.mode, require_user_password_on_create, custom_api_url)
- Exploit idea: Serve an app config with a custom_api_url and observe subsequent authenticated requests targeting it.
- Invariant to test: The API base URL must be pinned to a trusted set, not taken from a fetched config field.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: return custom_api_url pointing elsewhere and assert AppApi.getConfig does not send Authorization headers to that host.
