# Q2779: switchActiveUser reloads the wallet iframe mid-operation in toSearchParams.ts

## Question
UserApi.switchActiveUser calls embedded.reload(), which flushes the shared callback queue; can an attacker use toSearchParams (skips null/undefined to reject or hijack another in-flight wallet operation?

## Target
- File/function: [src/utils/toSearchParams.ts](src/utils/toSearchParams.ts) - toSearchParams (skips null/undefined, String() coercion)
- Entrypoint: PrivyInternal.getPath query building
- Attacker controls: query object values passed from public APIs
- Exploit idea: Start a signature, then switch users and observe the flush rejecting the pending callback.
- Invariant to test: A user switch must not be able to interfere with an unrelated in-flight signing operation.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: start a sign request, call toSearchParams (skips null/undefined, and assert the pending request neither resolves with foreign data nor leaks its callback.
