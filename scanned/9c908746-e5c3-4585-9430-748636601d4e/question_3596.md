# Q3596: cross-app login caches tokens before user confirmation in isCrossAppWalletSmart.ts

## Question
loginWithCrossAppAuth calls updateOnCrossAppAuthentication with the oauth_tokens as soon as the exchange returns; can an attacker cause a token to be cached for a provider app the user never approved through isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets?

## Target
- File/function: [src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts](src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts) - isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets
- Entrypoint: method selection between personal_sign and privy_signSmartWalletMessage
- Attacker controls: the address argument and duplicate addresses across accounts
- Exploit idea: Return oauth_tokens for a different provider in the exchange response.
- Invariant to test: Cached provider tokens must match the provider the user authorised.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: return a foreign provider token to isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets and assert it is not cached.
