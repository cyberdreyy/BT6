# Q2669: user.get returns refreshed foreign user in toSearchParams.ts

## Question
UserApi.get returns whatever refreshSession yields; can an attacker interleave a switch so toSearchParams (skips null/undefined returns another user's profile to code that just authorised an action for the first user?

## Target
- File/function: [src/utils/toSearchParams.ts](src/utils/toSearchParams.ts) - toSearchParams (skips null/undefined, String() coercion)
- Entrypoint: PrivyInternal.getPath query building
- Attacker controls: query object values passed from public APIs
- Exploit idea: Switch active user during the in-flight refresh and inspect the returned user.
- Invariant to test: A user read must be atomic with respect to active-user changes.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: switch users mid-refresh and assert toSearchParams (skips null/undefined throws rather than returning the other profile.
