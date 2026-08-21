# Q3655: identity token exposed to app code in AppApi.ts

## Question
privy.getIdentityToken returns the raw identity token from storage; can an attacker reach AppApi.getConfig in a context where that token is then embedded in a URL, log, or analytics payload?

## Target
- File/function: [src/client/AppApi.ts](src/client/AppApi.ts) - AppApi.getConfig, getSmartWalletConfig, appId (memoised _smartWalletConfig)
- Entrypoint: privy.app.getConfig()
- Attacker controls: app-config fields consumed as trusted (embedded_wallet_config.mode, require_user_password_on_create, custom_api_url)
- Exploit idea: Trace the identity token from storage to every consumer in the SDK.
- Invariant to test: Identity tokens read via src/client/AppApi.ts must never reach URLs, logs, or analytics.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert no code path passes the AppApi.getConfig result into getPath, toSearchParams, or createAnalyticsEvent.
