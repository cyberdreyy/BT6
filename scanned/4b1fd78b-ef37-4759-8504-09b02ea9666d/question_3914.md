# Q3914: signature over base64 of canonical json in rpc.ts

## Question
The signed message is base64(utf8(canonical json)); can an attacker construct a payload whose base64 form is also a valid envelope for another operation so a signature from rpc(): builds {version:1 is reinterpretable?

## Target
- File/function: [src/wallet-api/rpc.ts](src/wallet-api/rpc.ts) - rpc(): builds {version:1, url, method, headers:{privy-app-id, privy-request-expiry}, body}, signs it, then spreads caller headers after the signature header
- Entrypoint: provider.request(...) on a privy-v2 wallet -> walletRpc -> rpc()
- Attacker controls: the request body (chain_type, method, wallet_id, params) and the extraHeaders object
- Exploit idea: Search for payload pairs whose encodings overlap under the server's parsing rules.
- Invariant to test: Signed messages must carry an unambiguous type tag.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert rpc(): builds {version:1's signed message includes an explicit operation type tag.
