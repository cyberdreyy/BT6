# Q0299: response type checked but payload trusted in signTypedData.ts

## Question
sendCrossAppRequest validates privy_cross_app_type equals PRIVY_CROSS_APP_ACTION_RESPONSE and then returns privy_cross_app_payload verbatim; can an attacker return a payload through crossApp signTypedData: params [address that the app treats as a signature or transaction hash without any verification?

## Target
- File/function: [src/action/crossApp/wallet/signTypedData.ts](src/action/crossApp/wallet/signTypedData.ts) - crossApp signTypedData: params [address, generateDomainType(typedData)]
- Entrypoint: privy.crossApp.wallet.signTypedData({user, typedData, address, redirectUrl})
- Attacker controls: the whole typedData object including domain and types
- Exploit idea: Return a well-formed response with an arbitrary payload string.
- Invariant to test: A returned signature or hash must be verified against the request before being surfaced.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return an arbitrary payload from crossApp signTypedData: params [address and assert verification before it is returned.
