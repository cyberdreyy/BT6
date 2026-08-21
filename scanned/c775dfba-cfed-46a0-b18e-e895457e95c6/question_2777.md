# Q2777: switchActiveUser reloads the wallet iframe mid-operation in Error.ts

## Question
UserApi.switchActiveUser calls embedded.reload(), which flushes the shared callback queue; can an attacker use PrivyApiError to reject or hijack another in-flight wallet operation?

## Target
- File/function: [src/Error.ts](src/Error.ts) - PrivyApiError, PrivyClientError, MoonpayApiError, createErrorFormatter, errorIndicatesMfaCanceled
- Entrypoint: every catch path in the SDK
- Attacker controls: error.code / error.message strings returned by any reachable response
- Exploit idea: Start a signature, then switch users and observe the flush rejecting the pending callback.
- Invariant to test: A user switch must not be able to interfere with an unrelated in-flight signing operation.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: start a sign request, call PrivyApiError, and assert the pending request neither resolves with foreign data nor leaks its callback.
