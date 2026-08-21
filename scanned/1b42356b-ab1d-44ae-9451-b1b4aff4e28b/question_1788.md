# Q1788: debug logger prints session material in toAbortSignalTimeout.ts

## Question
The logger emits privy:refresh lines and error objects at DEBUG; can an attacker cause toAbortSignalTimeout (20s request abort signal) to write token or code material into a log sink the app forwards off-device?

## Target
- File/function: [src/toAbortSignalTimeout.ts](src/toAbortSignalTimeout.ts) - toAbortSignalTimeout (20s request abort signal)
- Entrypoint: PrivyInternal._beforeRequest* signal
- Attacker controls: request duration, abort timing versus storage writes
- Exploit idea: Enable DEBUG, run a refresh and a failed auth, and inspect the emitted lines.
- Invariant to test: No log line from src/toAbortSignalTimeout.ts may contain a token, verifier, or code value.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture logger output around toAbortSignalTimeout (20s request abort signal) and assert no stored credential substring appears.
