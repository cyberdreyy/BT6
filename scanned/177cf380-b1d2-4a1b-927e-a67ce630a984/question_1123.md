# Q1123: path params not escaped in Privy.ts

## Question
getPath compiles route params with getPathWithParams then appends toSearchParams output; can an attacker pass a param containing slashes or query characters so Privy constructor targets a different endpoint?

## Target
- File/function: [src/client/Privy.ts](src/client/Privy.ts) - Privy constructor, initialize, getAccessToken, getIdentityToken, setMessagePoster, fetchPrivyRoute, getCompiledPath, track
- Entrypoint: new Privy({appId, clientId, sessions, storage, ...}) and privy.fetchPrivyRoute(...)
- Attacker controls: constructor options, arbitrary route+body via fetchPrivyRoute, message poster injection
- Exploit idea: Call a param-taking route with '../' or '?x=' inside the param value.
- Invariant to test: Route parameters must be encoded so they cannot alter the request path.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: call Privy constructor with a param of '../other' and assert the compiled path stays within the intended route.
