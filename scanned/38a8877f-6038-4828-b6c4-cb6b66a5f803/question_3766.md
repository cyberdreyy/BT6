# Q3766: storeCustomerAccessToken accepts a non-string silently in logger.ts

## Question
Passing a non-string to the store methods deletes the entry and emits 'token_cleared'; can an attacker use logger levels NONE/ERROR/WARN/INFO/DEBUG to clear another session's credential by feeding a non-string through a reachable path?

## Target
- File/function: [src/client/logger.ts](src/client/logger.ts) - logger levels NONE/ERROR/WARN/INFO/DEBUG, privy:refresh debug lines
- Entrypoint: new Privy({logLevel: 'DEBUG'})
- Attacker controls: what the SDK writes to console at each level
- Exploit idea: Drive a response with a null token field and observe the deletion path.
- Invariant to test: Credential deletion must be an explicit operation, not the fallback for a malformed value.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass undefined through logger levels NONE/ERROR/WARN/INFO/DEBUG and assert it raises rather than silently deleting.
