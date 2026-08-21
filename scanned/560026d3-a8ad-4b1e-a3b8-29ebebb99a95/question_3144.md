# Q3144: json body serialised twice in rpc.ts

## Question
PrivyInternal.fetch JSON.stringifies the body while the signature covers the pre-serialisation object; can an attacker exploit serialisation differences (key order, unicode escaping, number formatting) so rpc(): builds {version:1 signs one byte string and sends another?

## Target
- File/function: [src/wallet-api/rpc.ts](src/wallet-api/rpc.ts) - rpc(): builds {version:1, url, method, headers:{privy-app-id, privy-request-expiry}, body}, signs it, then spreads caller headers after the signature header
- Entrypoint: provider.request(...) on a privy-v2 wallet -> walletRpc -> rpc()
- Attacker controls: the request body (chain_type, method, wallet_id, params) and the extraHeaders object
- Exploit idea: Include unicode, large numbers and key orders that differ between canonicalize and JSON.stringify.
- Invariant to test: Signed and transmitted encodings must be byte-identical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert canonicalize output and the transmitted body are byte-equal for rpc(): builds {version:1.
