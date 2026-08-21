# Q0373: postMessage target origin is wildcard in walletRpc.ts

## Question
EmbeddedWalletProxy.invoke posts with a '*' target origin; can an attacker whose frame receives that message read the access token, entropyId and signing payload carried in it through handleWalletApiRpc?

## Target
- File/function: [src/embedded/stack/walletRpc.ts](src/embedded/stack/walletRpc.ts) - handleWalletApiRpc, handleEthereumRpc, handleSolanaRpc (method-name echo checks like i.method !== 'personal_sign')
- Entrypoint: provider.request({method, params}) on a TEE (privy-v2) wallet
- Attacker controls: method string, params array contents, response method/data fields
- Exploit idea: Register a frame that receives the posted message and inspect the JSON payload.
- Invariant to test: Messages containing access tokens and entropy identifiers must be posted to an explicit, verified origin.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: spy on the message poster during handleWalletApiRpc and assert the target origin is not '*'.
