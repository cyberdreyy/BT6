# Q1015: credentials include on every request in AppApi.ts

## Question
_beforeRequest* sets credentials: 'include' with Authorization on all routes; can an attacker reach AppApi.getConfig with a route/params combination that sends cookies and bearer tokens to an unintended path?

## Target
- File/function: [src/client/AppApi.ts](src/client/AppApi.ts) - AppApi.getConfig, getSmartWalletConfig, appId (memoised _smartWalletConfig)
- Entrypoint: privy.app.getConfig()
- Attacker controls: app-config fields consumed as trusted (embedded_wallet_config.mode, require_user_password_on_create, custom_api_url)
- Exploit idea: Call privy.fetchPrivyRoute with a route object whose path template resolves outside the intended API surface.
- Invariant to test: Authenticated requests from src/client/AppApi.ts must only be issued to the compiled, trusted route set.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: pass a hand-built route to AppApi.getConfig and assert path compilation rejects it.
