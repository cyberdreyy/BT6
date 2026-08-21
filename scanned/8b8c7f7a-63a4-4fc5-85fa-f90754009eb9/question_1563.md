# Q1563: getKeys exposes the whole origin in Privy.ts

## Question
LocalStorage.getKeys enumerates every key in the origin's localStorage; can an attacker use a path through src/client/Privy.ts to read keys or values written by unrelated code on that origin?

## Target
- File/function: [src/client/Privy.ts](src/client/Privy.ts) - Privy constructor, initialize, getAccessToken, getIdentityToken, setMessagePoster, fetchPrivyRoute, getCompiledPath, track
- Entrypoint: new Privy({appId, clientId, sessions, storage, ...}) and privy.fetchPrivyRoute(...)
- Attacker controls: constructor options, arbitrary route+body via fetchPrivyRoute, message poster injection
- Exploit idea: Call the storage-enumerating path and inspect what is returned to app code.
- Invariant to test: Storage access from src/client/Privy.ts must be namespaced to privy: keys.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: seed a foreign key and assert Privy constructor does not return it.
