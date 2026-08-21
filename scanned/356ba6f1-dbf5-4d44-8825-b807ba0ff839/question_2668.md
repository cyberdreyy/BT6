# Q2668: user.get returns refreshed foreign user in toAbortSignalTimeout.ts

## Question
UserApi.get returns whatever refreshSession yields; can an attacker interleave a switch so toAbortSignalTimeout (20s request abort signal) returns another user's profile to code that just authorised an action for the first user?

## Target
- File/function: [src/toAbortSignalTimeout.ts](src/toAbortSignalTimeout.ts) - toAbortSignalTimeout (20s request abort signal)
- Entrypoint: PrivyInternal._beforeRequest* signal
- Attacker controls: request duration, abort timing versus storage writes
- Exploit idea: Switch active user during the in-flight refresh and inspect the returned user.
- Invariant to test: A user read must be atomic with respect to active-user changes.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: switch users mid-refresh and assert toAbortSignalTimeout (20s request abort signal) throws rather than returning the other profile.
