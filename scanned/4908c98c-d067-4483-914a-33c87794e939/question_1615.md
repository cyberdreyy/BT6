# Q1615: openAuthSession is an injected dependency in getCrossAppAccountByWalletAddress.ts

## Question
The action factories take openAuthSession from the caller; can an attacker supply an implementation through getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address that observes the authorization URL and the returned code?

## Target
- File/function: [src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts](src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts) - getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address
- Entrypoint: privy.crossApp.wallet.signMessage({address, ...})
- Attacker controls: the address argument and the set of cross_app accounts linked to the user
- Exploit idea: Inject a logging implementation and inspect what it sees.
- Invariant to test: The auth-session transport must be trusted and not carry credentials it can retain.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address does not pass reusable credentials through the injected transport.
