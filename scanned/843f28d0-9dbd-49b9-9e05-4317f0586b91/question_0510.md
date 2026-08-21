# Q0510: canonicalize failure path in sign-wallet-request.ts

## Question
generateAuthorizationSignature throws invalid_input when canonicalize returns undefined; can an attacker submit a payload through SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) containing a BigInt, function or circular structure so the error path is reached at a point where state was already mutated?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Submit an unserialisable field and observe where the failure lands.
- Invariant to test: Signature preparation must fail before any state mutation or network call.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit an unserialisable payload to SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) and assert no request is issued.
