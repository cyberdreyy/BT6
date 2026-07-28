# Q3980: zkSync address derivation can diverge src_vs_dst cross_chain_gap

## Question
On the zkSync deployment path, can an unprivileged actor rely on source-vs-destination bytecode separation together with a live cross-chain timing gap across different block times to make `EscrowFactoryZkSync.addressOfEscrowSrc()` or `addressOfEscrowDst()` disagree with `EscrowZkSync._validateImmutables()` or `MinimalProxyZkSync` runtime behavior, producing a clone that is funded at one address but only operable at another?

## Target
- File/function: `contracts/zkSync/EscrowFactoryZkSync.sol`, `contracts/zkSync/EscrowZkSync.sol`, `contracts/zkSync/ZkSyncLib.sol`, `contracts/zkSync/MinimalProxyZkSync.sol`
- Entrypoint: zkSync `addressOfEscrow*()` -> clone deployment -> runtime immutable validation
- Attacker controls: the full immutable hash salt, destination/source profile choice, and any pre-funding or live calls made to the predicted zkSync clone
- Exploit idea: Stress the differences between zkSync CREATE2 derivation, input-hash binding, and runtime proxy validation.
- Invariant to test: Factory-computed zkSync clone addresses must exactly match the runtime address that accepts those immutables.
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: On the zkSync profile, compute the clone address under source-vs-destination bytecode separation and a live cross-chain timing gap across different block times, fund and deploy it, then verify that runtime calls validate the same address.
