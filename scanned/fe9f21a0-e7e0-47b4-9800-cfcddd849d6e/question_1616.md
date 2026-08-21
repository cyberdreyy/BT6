# Q1616: openAuthSession is an injected dependency in isCrossAppWalletSmart.ts

## Question
The action factories take openAuthSession from the caller; can an attacker supply an implementation through isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets that observes the authorization URL and the returned code?

## Target
- File/function: [src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts](src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts) - isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets
- Entrypoint: method selection between personal_sign and privy_signSmartWalletMessage
- Attacker controls: the address argument and duplicate addresses across accounts
- Exploit idea: Inject a logging implementation and inspect what it sees.
- Invariant to test: The auth-session transport must be trusted and not carry credentials it can retain.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets does not pass reusable credentials through the injected transport.
