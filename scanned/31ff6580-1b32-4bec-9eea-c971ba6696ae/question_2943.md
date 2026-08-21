# Q2943: error payload rendered to the user in index.ts

## Question
When privy_cross_app_type is PRIVY_CROSS_APP_ACTION_ERROR the payload string becomes the error message; can an attacker return a payload through crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest that misleads the user into re-approving a malicious action?

## Target
- File/function: [src/action/crossApp/wallet/index.ts](src/action/crossApp/wallet/index.ts) - crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest
- Entrypoint: privy.crossApp.wallet.*
- Attacker controls: shared request pipeline and its response validation
- Exploit idea: Return a crafted error payload and inspect what the app displays.
- Invariant to test: Provider-supplied strings must not be rendered as trusted SDK messages.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest sanitises or ignores provider-supplied error text.
