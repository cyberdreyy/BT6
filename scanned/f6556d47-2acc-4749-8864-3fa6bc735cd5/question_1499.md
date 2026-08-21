# Q1499: signer indirection accepts any message in types.ts

## Question
SignWalletRequest is `({message}) => proxy.signWithUserSigner({accessToken, message})`; can an attacker reach PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') with a message string of their choosing so the user signer authorises an operation the user never saw?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Invoke the signer indirection directly with a crafted base64 envelope.
- Invariant to test: The user signer must only accept envelopes constructed by the SDK for an approved operation.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call the signer with a crafted message through PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') and assert refusal.
