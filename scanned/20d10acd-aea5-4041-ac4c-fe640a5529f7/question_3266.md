# Q3266: request json embedded unescaped in the url in isCrossAppWalletSmart.ts

## Question
The request object is JSON.stringified into a query parameter; can an attacker craft request content through isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets that alters the resulting URL structure?

## Target
- File/function: [src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts](src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts) - isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets
- Entrypoint: method selection between personal_sign and privy_signSmartWalletMessage
- Attacker controls: the address argument and duplicate addresses across accounts
- Exploit idea: Include characters that affect URL parsing in the request content.
- Invariant to test: URL parameters must be encoded so content cannot alter structure.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: include URL metacharacters in isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets's request and assert encoding.
