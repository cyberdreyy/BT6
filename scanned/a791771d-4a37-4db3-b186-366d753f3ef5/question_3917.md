# Q3917: signature over base64 of canonical json in get-wallet.ts

## Question
The signed message is base64(utf8(canonical json)); can an attacker construct a payload whose base64 form is also a valid envelope for another operation so a signature from getWallet(): WalletGet by wallet_id is reinterpretable?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Search for payload pairs whose encodings overlap under the server's parsing rules.
- Invariant to test: Signed messages must carry an unambiguous type tag.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert getWallet(): WalletGet by wallet_id's signed message includes an explicit operation type tag.
