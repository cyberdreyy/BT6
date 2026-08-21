# Q3988: saved-users JSON parsed leniently in toAbortSignalTimeout.ts

## Question
getSavedUserIds JSON.parses the stored list and filters to strings, returning [] on failure; can an attacker seed a crafted list so toAbortSignalTimeout (20s request abort signal) enumerates or switches to unintended ids?

## Target
- File/function: [src/toAbortSignalTimeout.ts](src/toAbortSignalTimeout.ts) - toAbortSignalTimeout (20s request abort signal)
- Entrypoint: PrivyInternal._beforeRequest* signal
- Attacker controls: request duration, abort timing versus storage writes
- Exploit idea: Write a list containing crafted ids and call the switch path.
- Invariant to test: The saved-user list must be integrity-checked against stored credentials.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: seed a foreign id and assert toAbortSignalTimeout (20s request abort signal) rejects a switch to it.
