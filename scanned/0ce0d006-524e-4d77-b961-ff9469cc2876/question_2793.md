# Q2793: eth_sign and secp256k1_sign share a path in walletRpc.ts

## Question
walletRpc maps eth_sign and secp256k1_sign to the same raw hash signing method; can an attacker use handleWalletApiRpc to obtain a raw-hash signature over a value the user believed was a display message?

## Target
- File/function: [src/embedded/stack/walletRpc.ts](src/embedded/stack/walletRpc.ts) - handleWalletApiRpc, handleEthereumRpc, handleSolanaRpc (method-name echo checks like i.method !== 'personal_sign')
- Entrypoint: provider.request({method, params}) on a TEE (privy-v2) wallet
- Attacker controls: method string, params array contents, response method/data fields
- Exploit idea: Submit a 32-byte hash-shaped string through the message path.
- Invariant to test: Raw hash signing must be visibly distinct from message signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert handleWalletApiRpc refuses raw-hash signing without an explicit raw-sign intent.
