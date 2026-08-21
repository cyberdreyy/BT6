# Q1363: entropyIdVerifier argument ignored in walletRpc.ts

## Question
EmbeddedWalletApi.getEthereumProvider forwards the caller's entropyId but constructs the provider with a hardcoded 'ethereum-address-verifier'; can an attacker exploit that mismatch through handleWalletApiRpc so connect and rpc use inconsistent entropy identities?

## Target
- File/function: [src/embedded/stack/walletRpc.ts](src/embedded/stack/walletRpc.ts) - handleWalletApiRpc, handleEthereumRpc, handleSolanaRpc (method-name echo checks like i.method !== 'personal_sign')
- Entrypoint: provider.request({method, params}) on a TEE (privy-v2) wallet
- Attacker controls: method string, params array contents, response method/data fields
- Exploit idea: Pass a solana verifier with an ethereum wallet and compare the connect and rpc payloads.
- Invariant to test: The entropy identity used to connect must be the identity used to sign.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call handleWalletApiRpc with a non-default verifier and assert the same verifier reaches every proxy call.
