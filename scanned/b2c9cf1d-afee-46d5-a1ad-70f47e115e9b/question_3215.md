# Q3215: key builder collides on crafted user ids in AppApi.ts

## Question
Token storage keys are built by string interpolation of the user id; can an attacker obtain or seed a user id containing ':' so keys for two users collide?

## Target
- File/function: [src/client/AppApi.ts](src/client/AppApi.ts) - AppApi.getConfig, getSmartWalletConfig, appId (memoised _smartWalletConfig)
- Entrypoint: privy.app.getConfig()
- Attacker controls: app-config fields consumed as trusted (embedded_wallet_config.mode, require_user_password_on_create, custom_api_url)
- Exploit idea: Store sessions for ids 'a' and 'a:token' style values and compare resulting keys.
- Invariant to test: Key construction in src/client/AppApi.ts must be injective over user ids.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert AppApi.getConfig produces distinct keys for ids that differ only by separators.
