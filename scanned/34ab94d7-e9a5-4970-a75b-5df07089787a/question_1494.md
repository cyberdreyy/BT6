# Q1494: signer indirection accepts any message in rpc.ts

## Question
SignWalletRequest is `({message}) => proxy.signWithUserSigner({accessToken, message})`; can an attacker reach rpc(): builds {version:1 with a message string of their choosing so the user signer authorises an operation the user never saw?

## Target
- File/function: [src/wallet-api/rpc.ts](src/wallet-api/rpc.ts) - rpc(): builds {version:1, url, method, headers:{privy-app-id, privy-request-expiry}, body}, signs it, then spreads caller headers after the signature header
- Entrypoint: provider.request(...) on a privy-v2 wallet -> walletRpc -> rpc()
- Attacker controls: the request body (chain_type, method, wallet_id, params) and the extraHeaders object
- Exploit idea: Invoke the signer indirection directly with a crafted base64 envelope.
- Invariant to test: The user signer must only accept envelopes constructed by the SDK for an approved operation.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call the signer with a crafted message through rpc(): builds {version:1 and assert refusal.
