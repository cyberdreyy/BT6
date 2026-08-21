# Q0025: unverified JWT decode drives identity in AppApi.ts

## Question
Token.parse uses jose.decodeJwt with no signature verification; can an unprivileged attacker reach AppApi.getConfig with a self-issued JWT-shaped string so its sub/exp fields are treated as identity or validity?

## Target
- File/function: [src/client/AppApi.ts](src/client/AppApi.ts) - AppApi.getConfig, getSmartWalletConfig, appId (memoised _smartWalletConfig)
- Entrypoint: privy.app.getConfig()
- Attacker controls: app-config fields consumed as trusted (embedded_wallet_config.mode, require_user_password_on_create, custom_api_url)
- Exploit idea: Place a crafted unsigned JWT where src/client/AppApi.ts reads a token and observe subject/expiry being consumed without verification.
- Invariant to test: No unverified JWT claim may determine identity, expiry or storage keying in src/client/AppApi.ts.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: hand AppApi.getConfig an unsigned JWT with an arbitrary sub and assert it is not accepted as an identity.
