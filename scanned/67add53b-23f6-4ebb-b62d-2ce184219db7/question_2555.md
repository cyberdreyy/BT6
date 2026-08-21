# Q2555: acceptTerms mutates without confirmation in AppApi.ts

## Question
UserApi.acceptTerms posts on behalf of the session with no argument; can an attacker trigger AppApi.getConfig from app code paths so terms are accepted without the user acting?

## Target
- File/function: [src/client/AppApi.ts](src/client/AppApi.ts) - AppApi.getConfig, getSmartWalletConfig, appId (memoised _smartWalletConfig)
- Entrypoint: privy.app.getConfig()
- Attacker controls: app-config fields consumed as trusted (embedded_wallet_config.mode, require_user_password_on_create, custom_api_url)
- Exploit idea: Call the method directly and observe the user object change.
- Invariant to test: State-changing user operations must require an explicit user action signal.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert AppApi.getConfig is not reachable from any automatic initialization path.
