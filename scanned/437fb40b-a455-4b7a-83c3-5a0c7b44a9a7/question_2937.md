# Q2937: error payload rendered to the user in throwIfNotLoggedIn.ts

## Question
When privy_cross_app_type is PRIVY_CROSS_APP_ACTION_ERROR the payload string becomes the error message; can an attacker return a payload through throwIfNotLoggedIn(user): only checks the user object passed by the caller that misleads the user into re-approving a malicious action?

## Target
- File/function: [src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts](src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts) - throwIfNotLoggedIn(user): only checks the user object passed by the caller
- Entrypoint: every crossApp.wallet action
- Attacker controls: the user object supplied by the caller rather than read from session
- Exploit idea: Return a crafted error payload and inspect what the app displays.
- Invariant to test: Provider-supplied strings must not be rendered as trusted SDK messages.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert throwIfNotLoggedIn(user): only checks the user object passed by the caller sanitises or ignores provider-supplied error text.
