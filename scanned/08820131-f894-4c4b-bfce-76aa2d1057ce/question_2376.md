# Q2376: wallet-api path compiled from route templates in create.ts

## Question
getCompiledPath interpolates wallet_id into the route path before it is signed; can an attacker supply a wallet_id containing path separators so create(): WalletCreate with optional privy-idempotency-key header signs and calls a different endpoint?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Pass a wallet id containing '/' or '%2F'.
- Invariant to test: Path parameters must be encoded before compilation and signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a separator-bearing wallet id to create(): WalletCreate with optional privy-idempotency-key header and assert encoding or rejection.
