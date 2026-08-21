# Q1496: signer indirection accepts any message in create.ts

## Question
SignWalletRequest is `({message}) => proxy.signWithUserSigner({accessToken, message})`; can an attacker reach create(): WalletCreate with optional privy-idempotency-key header with a message string of their choosing so the user signer authorises an operation the user never saw?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Invoke the signer indirection directly with a crafted base64 envelope.
- Invariant to test: The user signer must only accept envelopes constructed by the SDK for an approved operation.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call the signer with a crafted message through create(): WalletCreate with optional privy-idempotency-key header and assert refusal.
