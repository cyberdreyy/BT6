# Q0508: canonicalize failure path in update-wallet.ts

## Question
generateAuthorizationSignature throws invalid_input when canonicalize returns undefined; can an attacker submit a payload through updateWallet(): signs {version:1 containing a BigInt, function or circular structure so the error path is reached at a point where state was already mutated?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Submit an unserialisable field and observe where the failure lands.
- Invariant to test: Signature preparation must fail before any state mutation or network call.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit an unserialisable payload to updateWallet(): signs {version:1 and assert no request is issued.
