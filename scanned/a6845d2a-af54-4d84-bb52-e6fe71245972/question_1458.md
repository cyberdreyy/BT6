# Q1458: LocalStorage.get throws on non-JSON in toAbortSignalTimeout.ts

## Question
LocalStorage.get calls JSON.parse without guarding; can an attacker place a non-JSON value under a privy: key so every subsequent toAbortSignalTimeout (20s request abort signal) read throws and the SDK falls back to a less-safe path?

## Target
- File/function: [src/toAbortSignalTimeout.ts](src/toAbortSignalTimeout.ts) - toAbortSignalTimeout (20s request abort signal)
- Entrypoint: PrivyInternal._beforeRequest* signal
- Attacker controls: request duration, abort timing versus storage writes
- Exploit idea: Write a raw string under a privy: key from the same origin and observe the read path.
- Invariant to test: A malformed stored value must degrade safely without changing authentication behaviour.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: set a non-JSON value and assert toAbortSignalTimeout (20s request abort signal) treats it as absent rather than throwing into a fallback.
