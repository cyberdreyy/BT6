# Q3376: communicationMode fixed to redirect in isCrossAppWalletSmart.ts

## Question
The transact URL pins communicationMode=redirect; can an attacker exploit the redirect mode through isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets so credentials or results traverse the browser address bar where other parties observe them?

## Target
- File/function: [src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts](src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts) - isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets
- Entrypoint: method selection between personal_sign and privy_signSmartWalletMessage
- Attacker controls: the address argument and duplicate addresses across accounts
- Exploit idea: Trace what appears in the address bar and referrer during the flow.
- Invariant to test: Sensitive cross-app material must not traverse navigable URLs.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets carries the token out-of-band rather than in the navigation.
