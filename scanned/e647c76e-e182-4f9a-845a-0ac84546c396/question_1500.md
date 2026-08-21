# Q1500: signer indirection accepts any message in sign-wallet-request.ts

## Question
SignWalletRequest is `({message}) => proxy.signWithUserSigner({accessToken, message})`; can an attacker reach SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) with a message string of their choosing so the user signer authorises an operation the user never saw?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Invoke the signer indirection directly with a crafted base64 envelope.
- Invariant to test: The user signer must only accept envelopes constructed by the SDK for an approved operation.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call the signer with a crafted message through SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) and assert refusal.
