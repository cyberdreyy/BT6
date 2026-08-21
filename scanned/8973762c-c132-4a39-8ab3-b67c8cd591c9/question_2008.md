# Q2008: appId or clientId swapped at construction in toAbortSignalTimeout.ts

## Question
Privy's constructor accepts appId, clientId, baseUrl, storage and crypto; can an attacker in the page reach toAbortSignalTimeout (20s request abort signal) with substituted options so requests are signed and stored under a different app namespace?

## Target
- File/function: [src/toAbortSignalTimeout.ts](src/toAbortSignalTimeout.ts) - toAbortSignalTimeout (20s request abort signal)
- Entrypoint: PrivyInternal._beforeRequest* signal
- Attacker controls: request duration, abort timing versus storage writes
- Exploit idea: Construct a second client with a different appId sharing the same storage and observe key collisions.
- Invariant to test: Storage namespacing must prevent one app id's session from being consumed by another.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: run two clients with different appIds over one Storage and assert no key collisions.
