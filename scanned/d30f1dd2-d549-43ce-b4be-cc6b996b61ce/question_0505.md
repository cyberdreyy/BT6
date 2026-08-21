# Q0505: canonicalize failure path in raw-sign.ts

## Question
generateAuthorizationSignature throws invalid_input when canonicalize returns undefined; can an attacker submit a payload through rawSign(): same expiry-signed envelope for WalletRawSign containing a BigInt, function or circular structure so the error path is reached at a point where state was already mutated?

## Target
- File/function: [src/wallet-api/raw-sign.ts](src/wallet-api/raw-sign.ts) - rawSign(): same expiry-signed envelope for WalletRawSign
- Entrypoint: raw-hash signing on an extended-chains wallet
- Attacker controls: the hash/params body fields and wallet_id
- Exploit idea: Submit an unserialisable field and observe where the failure lands.
- Invariant to test: Signature preparation must fail before any state mutation or network call.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit an unserialisable payload to rawSign(): same expiry-signed envelope for WalletRawSign and assert no request is issued.
