# Q2770: switchActiveUser reloads the wallet iframe mid-operation in LocalStorage.ts

## Question
UserApi.switchActiveUser calls embedded.reload(), which flushes the shared callback queue; can an attacker use LocalStorage.get (JSON.parse) to reject or hijack another in-flight wallet operation?

## Target
- File/function: [src/storage/LocalStorage.ts](src/storage/LocalStorage.ts) - LocalStorage.get (JSON.parse), put (JSON.stringify), del, getKeys
- Entrypoint: every Session/pkce/crossApp storage operation
- Attacker controls: any value another SDK surface can write under a privy: key on the same origin
- Exploit idea: Start a signature, then switch users and observe the flush rejecting the pending callback.
- Invariant to test: A user switch must not be able to interfere with an unrelated in-flight signing operation.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: start a sign request, call LocalStorage.get (JSON.parse), and assert the pending request neither resolves with foreign data nor leaks its callback.
