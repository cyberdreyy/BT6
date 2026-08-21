# Q0507: canonicalize failure path in get-wallet.ts

## Question
generateAuthorizationSignature throws invalid_input when canonicalize returns undefined; can an attacker submit a payload through getWallet(): WalletGet by wallet_id containing a BigInt, function or circular structure so the error path is reached at a point where state was already mutated?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Submit an unserialisable field and observe where the failure lands.
- Invariant to test: Signature preparation must fail before any state mutation or network call.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit an unserialisable payload to getWallet(): WalletGet by wallet_id and assert no request is issued.
