# Q1617: openAuthSession is an injected dependency in throwIfNotLoggedIn.ts

## Question
The action factories take openAuthSession from the caller; can an attacker supply an implementation through throwIfNotLoggedIn(user): only checks the user object passed by the caller that observes the authorization URL and the returned code?

## Target
- File/function: [src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts](src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts) - throwIfNotLoggedIn(user): only checks the user object passed by the caller
- Entrypoint: every crossApp.wallet action
- Attacker controls: the user object supplied by the caller rather than read from session
- Exploit idea: Inject a logging implementation and inspect what it sees.
- Invariant to test: The auth-session transport must be trusted and not carry credentials it can retain.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert throwIfNotLoggedIn(user): only checks the user object passed by the caller does not pass reusable credentials through the injected transport.
