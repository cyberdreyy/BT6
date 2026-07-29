[1](#0-0) [2](#0-1) [3](#0-2) The external bug report highlights a vulnerability where a contract stores a `protocolFeeDestination` address during initialization instead of dynamically retrieving it from a factory. If the factory's fee destination is updated (e.g., due to compromise), the existing contracts continue to send fees to the old, potentially compromised address.

In the 1inch Cross-chain Swap repository, a similar pattern exists in the fee routing logic. Fees (protocol and integrator) are not retrieved from a central factory state at the time of withdrawal. Instead, the `protocolFeeRecipient` and `integratorFeeRecipient` are passed as part of the `extraData` during order filling in `BaseEscrowFactory._postInteraction` and are permanently baked into the `immutables.parameters` of the `EscrowDst` contract.

Specifically:
1. In `BaseEscrowFactory.sol`, the `_postInteraction` function (called when a resolver fills an order) extracts `protocolFeeRecipient` and `integratorFeeRecipient` from the `extraData` provided by the taker (resolver).
2. These addresses are encoded into the `parameters` field of the `IBaseEscrow.Immutables` struct.
3. The `EscrowDst` contract is a minimal proxy (EIP-1167) where the behavior is governed by these immutable parameters appended to its bytecode.
4. When `EscrowDst.withdraw` is called, it retrieves the fee recipients directly from its immutable `parameters` using `ImmutablesLib.protocolFeeRecipientCd()` and transfers the fees.

This matches the "unprivileged-user model" root cause: the taker (an unprivileged actor in the context of protocol governance) provides the fee recipient addresses at the time of escrow creation. If a malicious or compromised resolver provides their own address as the `protocolFeeRecipient`, or if the protocol's official recipient address changes, the escrow is locked to the addresses provided at creation. While the factory itself doesn't seem to have a `setProtocolFeeRecipient` function (it's passed per-order), the core issue is that the fee routing is determined by the taker at the moment of deployment and cannot be redirected by the protocol if those addresses are later deemed incorrect or compromised.

### Title
Taker-controlled protocol fee redirection via immutable escrow parameters - (File: contracts/BaseEscrowFactory.sol)

### Summary
The `EscrowDst` contract determines its protocol and integrator fee recipients based on immutable parameters set during deployment. These parameters are derived from `extraData` provided by the taker (resolver) during the `_postInteraction` call in `BaseEscrowFactory`. Because these addresses are baked into the contract's immutable state and not verified against a trusted protocol registry or dynamically retrieved, a malicious taker can provide their own addresses as fee recipients, effectively stealing fees meant for the protocol or integrator.

### Finding Description
In `BaseEscrowFactory._postInteraction`, the fee recipients are extracted from the `extraData` argument: [1](#0-0) 

These recipients are then encoded into the `parameters` of the `immutablesComplement`, which are used to deploy the `EscrowDst` contract: [2](#0-1) 

When `EscrowDst.withdraw` is executed, the contract retrieves these recipients from its own immutable storage: [3](#0-2) 

The `extraData` is provided by the taker when they call `fillOrder` on the Limit Order Protocol. There is no validation in `BaseEscrowFactory` to ensure that the `protocolFeeRecipient` matches the official 1inch fee bank or any authorized address. Consequently, the taker can redirect all fees to any address they control.

### Impact Explanation
This leads to the theft of unclaimed fee-like value. A malicious resolver can successfully complete swaps while paying 0% fees to the protocol and integrator by setting the recipient addresses to themselves. This fits the High severity impact: "theft or permanent loss of unclaimed fee-like value".

### Likelihood Explanation
The likelihood is high as it requires no special privileges; any resolver can craft the `extraData` to include their own addresses. The protocol's reliance on taker-supplied data for fee routing without validation is a direct architectural flaw.

### Recommendation
The `BaseEscrowFactory` should maintain a registry of authorized protocol fee recipients or use a hardcoded/constant address for the protocol fee bank. Instead of accepting the `protocolFeeRecipient` from `extraData`, it should be fetched from the factory's state or a trusted contract.

### Proof of Concept
1. A Maker signs an order with a 1% protocol fee.
2. A Malicious Resolver calls `fillOrder` on the LOP.
3. In the `extraData`, the Resolver provides their own address `0xAttacker` for both `integratorFeeRecipient` and `protocolFeeRecipient`.
4. `BaseEscrowFactory._postInteraction` is triggered. It encodes `0xAttacker` into the `EscrowDst` immutables.
5. The Resolver deploys `EscrowDst` with these immutables.
6. When the swap is settled via `EscrowDst.withdraw`, the 1% fee is transferred to `0xAttacker` instead of the 1inch fee bank.
7. The protocol loses the fee, and the resolver effectively avoids the protocol cost.

### Citations

**File:** contracts/BaseEscrowFactory.sol (L77-78)
```text
        address integratorFeeRecipient = address(bytes20(extraData[:20]));
        address protocolFeeRecipient = address(bytes20(extraData[20:40]));
```

**File:** contracts/BaseEscrowFactory.sol (L145-151)
```text
            parameters: abi.encode(
                protocolFeeAmount,
                integratorFeeAmount,
                protocolFeeRecipient,
                integratorFeeRecipient
            )
        });
```

**File:** contracts/EscrowDst.sol (L89-91)
```text
        if (protocolFeeAmount > 0) {
            _uniTransfer(immutables.token.get(), immutables.protocolFeeRecipientCd().get(), protocolFeeAmount);
        }
```
