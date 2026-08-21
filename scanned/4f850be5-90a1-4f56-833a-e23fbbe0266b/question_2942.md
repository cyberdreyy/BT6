# Q2942: error payload rendered to the user in index.ts

## Question
When privy_cross_app_type is PRIVY_CROSS_APP_ACTION_ERROR the payload string becomes the error message; can an attacker return a payload through crossApp action barrel: loginWithCrossAppAuth that misleads the user into re-approving a malicious action?

## Target
- File/function: [src/action/crossApp/index.ts](src/action/crossApp/index.ts) - crossApp action barrel: loginWithCrossAppAuth, linkWithCrossAppAuth, wallet.{signMessage,signTypedData,sendTransaction}
- Entrypoint: privy.crossApp.*
- Attacker controls: which dependency object (client, openAuthSession) is bound to each action
- Exploit idea: Return a crafted error payload and inspect what the app displays.
- Invariant to test: Provider-supplied strings must not be rendered as trusted SDK messages.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert crossApp action barrel: loginWithCrossAppAuth sanitises or ignores provider-supplied error text.
