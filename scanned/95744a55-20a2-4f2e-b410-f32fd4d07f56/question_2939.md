# Q2939: error payload rendered to the user in signTypedData.ts

## Question
When privy_cross_app_type is PRIVY_CROSS_APP_ACTION_ERROR the payload string becomes the error message; can an attacker return a payload through crossApp signTypedData: params [address that misleads the user into re-approving a malicious action?

## Target
- File/function: [src/action/crossApp/wallet/signTypedData.ts](src/action/crossApp/wallet/signTypedData.ts) - crossApp signTypedData: params [address, generateDomainType(typedData)]
- Entrypoint: privy.crossApp.wallet.signTypedData({user, typedData, address, redirectUrl})
- Attacker controls: the whole typedData object including domain and types
- Exploit idea: Return a crafted error payload and inspect what the app displays.
- Invariant to test: Provider-supplied strings must not be rendered as trusted SDK messages.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert crossApp signTypedData: params [address sanitises or ignores provider-supplied error text.
