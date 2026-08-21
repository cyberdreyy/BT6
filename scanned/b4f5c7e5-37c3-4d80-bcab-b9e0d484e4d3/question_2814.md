# Q2814: expiry header name is a constant string in rpc.ts

## Question
PRIVY_REQUEST_EXPIRY_HEADER_NAME is spread into the header object by computed key; can an attacker inject a header of the same name through the extraHeaders path in rpc(): builds {version:1 so the transmitted expiry differs from the signed one?

## Target
- File/function: [src/wallet-api/rpc.ts](src/wallet-api/rpc.ts) - rpc(): builds {version:1, url, method, headers:{privy-app-id, privy-request-expiry}, body}, signs it, then spreads caller headers after the signature header
- Entrypoint: provider.request(...) on a privy-v2 wallet -> walletRpc -> rpc()
- Attacker controls: the request body (chain_type, method, wallet_id, params) and the extraHeaders object
- Exploit idea: Pass privy-request-expiry in extraHeaders and compare the signed and sent values.
- Invariant to test: The transmitted expiry must equal the signed expiry.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a conflicting expiry to rpc(): builds {version:1 and assert the request is rejected.
