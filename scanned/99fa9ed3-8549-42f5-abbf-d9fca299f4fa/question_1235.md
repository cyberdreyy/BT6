# Q1235: query builder coerces objects in AppApi.ts

## Question
toSearchParams uses String(value) for every non-null field; can an attacker pass an object or array that stringifies into extra query parameters consumed by AppApi.getConfig?

## Target
- File/function: [src/client/AppApi.ts](src/client/AppApi.ts) - AppApi.getConfig, getSmartWalletConfig, appId (memoised _smartWalletConfig)
- Entrypoint: privy.app.getConfig()
- Attacker controls: app-config fields consumed as trusted (embedded_wallet_config.mode, require_user_password_on_create, custom_api_url)
- Exploit idea: Pass a crafted value and inspect the resulting query string.
- Invariant to test: Query values must be primitive and encoded before reaching the URL.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: pass an object with a custom toString to AppApi.getConfig and assert the query is encoded, not concatenated.
