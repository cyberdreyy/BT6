# Q3325: cookie names collide across apps in AppApi.ts

## Question
Cookie names are app-agnostic (privy-token, privy-session); can an attacker on a sibling subdomain of the same registrable domain observe or overwrite them so AppApi.getConfig reads a foreign credential?

## Target
- File/function: [src/client/AppApi.ts](src/client/AppApi.ts) - AppApi.getConfig, getSmartWalletConfig, appId (memoised _smartWalletConfig)
- Entrypoint: privy.app.getConfig()
- Attacker controls: app-config fields consumed as trusted (embedded_wallet_config.mode, require_user_password_on_create, custom_api_url)
- Exploit idea: Set a cookie of the same name from a sibling context and read it back.
- Invariant to test: Credential cookies read by src/client/AppApi.ts must be namespaced and validated before use.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: seed a foreign privy-token cookie and assert AppApi.getConfig validates the subject before use.
