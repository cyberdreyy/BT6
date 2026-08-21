# Q2816: expiry header name is a constant string in create.ts

## Question
PRIVY_REQUEST_EXPIRY_HEADER_NAME is spread into the header object by computed key; can an attacker inject a header of the same name through the extraHeaders path in create(): WalletCreate with optional privy-idempotency-key header so the transmitted expiry differs from the signed one?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Pass privy-request-expiry in extraHeaders and compare the signed and sent values.
- Invariant to test: The transmitted expiry must equal the signed expiry.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a conflicting expiry to create(): WalletCreate with optional privy-idempotency-key header and assert the request is rejected.
