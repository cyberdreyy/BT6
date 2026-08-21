# Q0060: chain silently switched by tx.chainId in generateDomainType.ts

## Question
EmbeddedWalletProvider.ensureChainId calls internalSwitchEthereumChain with the chainId found in the request params; can an unprivileged attacker submit a transaction through cross-app privy.crossApp.wallet.signTypedData({typedData, ...}) whose chainId silently repoints the provider and the RPC client to another chain before signing?

## Target
- File/function: [src/utils/typedData/generateDomainType.ts](src/utils/typedData/generateDomainType.ts) - generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)
- Entrypoint: cross-app privy.crossApp.wallet.signTypedData({typedData, ...})
- Attacker controls: the typedData.domain and typedData.types objects
- Exploit idea: Send eth_sendTransaction with a chainId the user never selected and observe chainChanged plus the new client.
- Invariant to test: The provider chain must only change through an explicit wallet_switchEthereumChain approval.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) with a foreign chainId and assert no silent switch and no signature.
