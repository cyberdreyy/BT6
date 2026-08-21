# Q1013: credentials include on every request in Privy.ts

## Question
_beforeRequest* sets credentials: 'include' with Authorization on all routes; can an attacker reach Privy constructor with a route/params combination that sends cookies and bearer tokens to an unintended path?

## Target
- File/function: [src/client/Privy.ts](src/client/Privy.ts) - Privy constructor, initialize, getAccessToken, getIdentityToken, setMessagePoster, fetchPrivyRoute, getCompiledPath, track
- Entrypoint: new Privy({appId, clientId, sessions, storage, ...}) and privy.fetchPrivyRoute(...)
- Attacker controls: constructor options, arbitrary route+body via fetchPrivyRoute, message poster injection
- Exploit idea: Call privy.fetchPrivyRoute with a route object whose path template resolves outside the intended API surface.
- Invariant to test: Authenticated requests from src/client/Privy.ts must only be issued to the compiled, trusted route set.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: pass a hand-built route to Privy constructor and assert path compilation rejects it.
