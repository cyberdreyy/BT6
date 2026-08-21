# Q3105: require_user_password_on_create bypass in AppApi.ts

## Question
The password requirement is enforced client-side from config.require_user_password_on_create; can an attacker bypass it through AppApi.getConfig by supplying a recoveryMethod that skips the check?

## Target
- File/function: [src/client/AppApi.ts](src/client/AppApi.ts) - AppApi.getConfig, getSmartWalletConfig, appId (memoised _smartWalletConfig)
- Entrypoint: privy.app.getConfig()
- Attacker controls: app-config fields consumed as trusted (embedded_wallet_config.mode, require_user_password_on_create, custom_api_url)
- Exploit idea: Call create with an explicit recoveryMethod while the config requires a password.
- Invariant to test: Recovery-strength requirements must not be bypassable by argument choice in src/client/AppApi.ts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: set require_user_password_on_create and call AppApi.getConfig with each recoveryMethod, asserting the requirement holds.
