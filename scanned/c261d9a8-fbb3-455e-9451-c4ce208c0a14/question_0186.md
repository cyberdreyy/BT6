# Q0186: provider api url comes from the connections list in isCrossAppWalletSmart.ts

## Question
The transact URL host is provider_app_custom_api_url taken from the getCrossAppConnections response; can an attacker influence that value so isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets sends the provider access token and the request payload to a host of their choosing?

## Target
- File/function: [src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts](src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts) - isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets
- Entrypoint: method selection between personal_sign and privy_signSmartWalletMessage
- Attacker controls: the address argument and duplicate addresses across accounts
- Exploit idea: Return a connections entry with an attacker host and observe the outbound request.
- Invariant to test: Cross-app endpoints must be validated against a trusted registry before credentials are attached.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: return a crafted provider_app_custom_api_url and assert isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets refuses to send the token.
