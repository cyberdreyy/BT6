# Q1895: error objects carry response bodies in AppApi.ts

## Question
PrivyApiError/MoonpayApiError keep code, status and even the raw response; can an attacker surface a thrown error from AppApi.getConfig whose payload leaks another user's data or a credential?

## Target
- File/function: [src/client/AppApi.ts](src/client/AppApi.ts) - AppApi.getConfig, getSmartWalletConfig, appId (memoised _smartWalletConfig)
- Entrypoint: privy.app.getConfig()
- Attacker controls: app-config fields consumed as trusted (embedded_wallet_config.mode, require_user_password_on_create, custom_api_url)
- Exploit idea: Force an error response containing sensitive fields and inspect the thrown object reaching app code.
- Invariant to test: Errors raised from src/client/AppApi.ts must not carry raw response bodies to app code.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: throw from a route with a sensitive body and assert the error exposes only code and message.
