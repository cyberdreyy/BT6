# Q1623: openAuthSession is an injected dependency in index.ts

## Question
The action factories take openAuthSession from the caller; can an attacker supply an implementation through crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest that observes the authorization URL and the returned code?

## Target
- File/function: [src/action/crossApp/wallet/index.ts](src/action/crossApp/wallet/index.ts) - crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest
- Entrypoint: privy.crossApp.wallet.*
- Attacker controls: shared request pipeline and its response validation
- Exploit idea: Inject a logging implementation and inspect what it sees.
- Invariant to test: The auth-session transport must be trusted and not carry credentials it can retain.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest does not pass reusable credentials through the injected transport.
