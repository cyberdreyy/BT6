# Q2483: headers object shared across retries in generate-authorization-signature.ts

## Question
fetch-retry replays the same request object up to three times; can an attacker exploit the retry of a signed wallet operation in generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64 so one authorization signature yields several executions?

## Target
- File/function: [src/wallet-api/generate-authorization-signature.ts](src/wallet-api/generate-authorization-signature.ts) - generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64, or raw base64 for Uint8Array
- Entrypoint: every TEE wallet-api request signed with the user signer
- Attacker controls: the payload object fields that reach canonicalize, and any field canonicalize drops
- Exploit idea: Return a retryable status for a signing request and count executions.
- Invariant to test: A signed wallet operation must execute at most once.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: return 503 then 200 for generateAuthorizationSignature: canonicalize(payload) -> utf8 -> base64's route and assert one execution.
