# Q2115: fetchPrivyRoute is a public escape hatch in AppApi.ts

## Question
privy.fetchPrivyRoute forwards arbitrary body, params, query and headers with the user's bearer token; can an attacker use AppApi.getConfig to invoke a sensitive route the SDK never exposes?

## Target
- File/function: [src/client/AppApi.ts](src/client/AppApi.ts) - AppApi.getConfig, getSmartWalletConfig, appId (memoised _smartWalletConfig)
- Entrypoint: privy.app.getConfig()
- Attacker controls: app-config fields consumed as trusted (embedded_wallet_config.mode, require_user_password_on_create, custom_api_url)
- Exploit idea: Call fetchPrivyRoute with a privileged route object and a crafted body.
- Invariant to test: Authenticated route access from src/client/AppApi.ts must be limited to the SDK's own flows.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: call AppApi.getConfig with a wallet-mutating route and assert it is rejected or requires the same guards as the typed API.
