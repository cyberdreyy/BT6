# Q1225: cookie twin outlives storage clear in SmartWalletApi.ts

## Question
Session writes cookie twins (privy-token, privy-refresh-token, privy-id-token, privy-session) when server cookies are off; can an attacker make SmartWalletApi.init leave a live cookie after the storage entries were cleared?

## Target
- File/function: [src/client/auth/SmartWalletApi.ts](src/client/auth/SmartWalletApi.ts) - SmartWalletApi.init, link, generateSmartWalletSiweMessage
- Entrypoint: privy.auth.smartWallet.init(wallet) then link(message, signature, type, version)
- Attacker controls: message override, signature, smart_wallet_type, smart_wallet_version, chainId
- Exploit idea: Force _isUsingServerCookies to flip between the login and the clear, then inspect document.cookie.
- Invariant to test: Cookie and storage credential copies must be created and destroyed under the same condition.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: toggle session.isUsingServerCookies between SmartWalletApi.init and destroyLocalState and assert js-cookie remove was called for every name.
