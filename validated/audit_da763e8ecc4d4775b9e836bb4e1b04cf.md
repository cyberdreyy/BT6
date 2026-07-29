### Title
Immutables-locked fee recipients prevent protocol and integrator fee updates - ([File: contracts/EscrowDst.sol])

### Summary
The `EscrowDst` contract relies on fee recipient addresses stored within the `Immutables` struct, which is hashed to determine the contract's `CREATE2` address. Similar to the seed report where `withdrawalWallet` is unchangeable, the `protocolFeeRecipient` and `integratorFeeRecipient` in this protocol are bound to the specific escrow instance at deployment. If an admin or integrator needs to rotate their fee-collection wallets (e.g., due to a compromise or operational change), existing escrows cannot be updated, and the `EscrowFactory` will continue to generate deterministic addresses for new escrows based on the old recipients if the `orderHash` and other swap parameters remain the same.

### Finding Description
In the 1inch Cross-chain Swap protocol, the `EscrowFactory` deploys `EscrowSrc` and `EscrowDst` as deterministic clones using `CREATE2` [1](#0-0) . The salt for these deployments is derived from the `Immutables` struct hash [2](#0-1) . 

For the destination chain, the `EscrowDst` contract extracts fee recipients from the `parameters` field of the `Immutables` struct [3](#0-2) . These parameters are encoded by the `BaseEscrowFactory` during the source chain's `postInteraction` and include the `protocolFeeRecipient` and `integratorFeeRecipient` [4](#0-3) . 

Because these addresses are part of the data that defines the escrow's identity (the salt), they are immutable for any specific swap instance. If a fee recipient address becomes invalid or compromised, there is no mechanism to redirect the fees within the `EscrowDst` contract. Furthermore, because the `EscrowFactory` on the destination chain validates the `Immutables` against the deterministic address [5](#0-4) , a user cannot simply provide a different recipient address during withdrawal, as it would change the hash and cause the `onlyValidImmutables` modifier to revert [6](#0-5) .

### Impact Explanation
The impact is a **Medium** severity issue. It results in a "smart contract unable to operate" correctly regarding its fee-routing logic if a recipient address must be changed. If a protocol or integrator wallet is compromised, funds meant for fees will be permanently routed to the compromised address for any orders already signed or escrows already deployed. This matches the "theft of coins/tokens meant for transaction fees" impact category, as an unprivileged actor (the maker/taker) is forced to send fees to an outdated/compromised address defined at the start of the swap lifecycle.

### Likelihood Explanation
The likelihood is **Low** but the architectural constraint is certain. It requires a specific operational need to change a fee recipient address during the lifecycle of an active or pending cross-chain swap. Since cross-chain swaps involve timelocks, the window of exposure where fees are locked into a specific address can be significant.

### Recommendation
Update the architecture to decouple fee recipients from the immutable salt. Instead of hardcoding recipients in the `Immutables` struct, the `EscrowDst` contract should query the `EscrowFactory` or a dedicated `FeeRegistry` for the current authorized `protocolFeeRecipient` and `integratorFeeRecipient`.

### Proof of Concept
1. A maker signs an order where the `extraData` specifies `ProtocolWallet_A` as the recipient [7](#0-6) .
2. The `EscrowSrc` is deployed, and the `SrcEscrowCreated` event broadcasts the `DstImmutables` containing `ProtocolWallet_A` in the `parameters` [8](#0-7) .
3. Before the taker calls `createDstEscrow`, the protocol admin identifies that `ProtocolWallet_A` is compromised and wants to switch to `ProtocolWallet_B`.
4. The taker deploys `EscrowDst`. The address is fixed based on the hash of immutables containing `ProtocolWallet_A` [9](#0-8) .
5. When `withdraw` is called, the contract executes `_uniTransfer` specifically to the address stored in the immutables [10](#0-9) .
6. There is no way for the admin to intervene, and the fees are lost to the compromised `ProtocolWallet_A`.

### Citations

**File:** contracts/BaseEscrowFactory.sol (L78-78)
```text
        address protocolFeeRecipient = address(bytes20(extraData[20:40]));
```

**File:** contracts/BaseEscrowFactory.sol (L145-153)
```text
            parameters: abi.encode(
                protocolFeeAmount,
                integratorFeeAmount,
                protocolFeeRecipient,
                integratorFeeRecipient
            )
        });

        emit SrcEscrowCreated(immutables, immutablesComplement);
```

**File:** contracts/BaseEscrowFactory.sol (L155-155)
```text
        bytes32 salt = immutables.hashMem();
```

**File:** contracts/BaseEscrowFactory.sol (L156-156)
```text
        address escrow = _deployEscrow(salt, 0, ESCROW_SRC_IMPLEMENTATION);
```

**File:** contracts/BaseEscrowFactory.sol (L178-179)
```text
        bytes32 salt = immutables.hashMem();
        address escrow = _deployEscrow(salt, msg.value, ESCROW_DST_IMPLEMENTATION);
```

**File:** contracts/EscrowDst.sol (L81-81)
```text
        onlyValidImmutables(immutables.hash())
```

**File:** contracts/EscrowDst.sol (L84-91)
```text
        uint256 integratorFeeAmount = immutables.integratorFeeAmountCd();
        uint256 protocolFeeAmount = immutables.protocolFeeAmountCd();
        if (integratorFeeAmount > 0) {
            _uniTransfer(immutables.token.get(), immutables.integratorFeeRecipientCd().get(), integratorFeeAmount);
        }
        if (protocolFeeAmount > 0) {
            _uniTransfer(immutables.token.get(), immutables.protocolFeeRecipientCd().get(), protocolFeeAmount);
        }
```

**File:** contracts/Escrow.sol (L24-28)
```text
    function _validateImmutables(bytes32 immutablesHash) internal view virtual override {
        if (Create2.computeAddress(immutablesHash, PROXY_BYTECODE_HASH, FACTORY) != address(this)) {
            revert InvalidImmutables();
        }
    }
```
