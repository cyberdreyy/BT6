## Title
Reentrancy via arbitrary `postInteraction` target corrupts shared Merkle validation state before it is read - (`File: contracts/BaseEscrowFactory.sol`)

### Summary
`BaseEscrowFactory._postInteraction` invokes an attacker-controllable external contract (the `tail` custom-postInteraction target embedded in order `extraData`) *before* it reads the shared `lastValidated[key]` mapping that determines which hashlock/index is bound to the escrow being deployed. Since no reentrancy guard exists anywhere in the codebase, this external call can re-enter the Limit Order Protocol (LOP) to trigger another partial fill of the *same* multiple-fill order, which overwrites `lastValidated[key]` via `MerkleStorageInvalidator.takerInteraction` before the original call resumes and reads it. This mirrors the MIMO `rebalance` bug: a shared piece of state (there, vault B's value; here, `lastValidated[key]` for one order) is read only *after* an externally-triggered, attacker-controlled call has had the opportunity to mutate it mid-flight.

### Finding Description
In `_postInteraction`: [1](#0-0) 

- Lines 94-105 make an external call to an arbitrary address decoded straight from calldata (`tail`), before any of the Merkle validation state is consulted.
- Only after that external call returns does the code read `lastValidated[key]` (line 118) and validate `_isValidPartialFill` against it (line 120), then use `validated.leaf` as the `hashlock` bound to the newly deployed `EscrowSrc`.
- `lastValidated[key]` is written by `MerkleStorageInvalidator.takerInteraction`, which is called directly by the LOP as part of a normal `fillOrderArgs` execution: [2](#0-1) 

If the external call at lines 94-105 re-enters the LOP to perform another partial fill of the same order (a legitimate, LOP-supported operation for multi-fill orders), `takerInteraction` will be invoked again for the *same* `key = keccak256(orderHash, rootShortened)`, overwriting `lastValidated[key]` with the index/leaf belonging to the *nested* fill. When the outer `_postInteraction` call resumes at line 118, it reads this now-corrupted value and binds the wrong hashlock/index to the escrow it is deploying for the *outer* fill's `makingAmount`.

A global `grep` confirms there is no `nonReentrant`/`ReentrancyGuard` anywhere in the repository, so nothing currently blocks this ordering. [3](#0-2) 

### Impact Explanation
The broken invariant is that `lastValidated[key]` at the point of use in `_postInteraction` should reflect the validation performed for *this specific* `takerInteraction`/`postInteraction` pair within the current fill, not a value overwritten by an interleaved fill of the same order. Concretely corrupted values:
- `validated.leaf` (used as `hashlock` for the deployed `EscrowSrc`) can end up being the leaf/secret-hash for a different partial-fill index than the `makingAmount`/`remainingMakingAmount` actually being processed.
- `validated.index` fed into `_isValidPartialFill` no longer corresponds to the outer fill, which can either wrongly pass a bogus index/amount pairing or wrongly reject/permanently desynchronize subsequent legitimate partial fills of the order (denial of further fills, i.e., funds/order becoming unfillable — a business-logic failure impacting the live swap lifecycle).

I was not able to fully trace this to a confirmed direct fund-theft outcome because I did not have `EscrowSrc.sol`'s exact `withdraw`/`onlyCaller` access-control logic in front of me to determine who can actually redeem an escrow whose hashlock ends up bound to a secret already known to the malicious order-maker. Depending on that logic, the consequence is at minimum a broken/frozen partial-fill order (Medium: contract unable to operate correctly due to state corrupted by an unprivileged actor), and potentially higher if it allows early/incorrect fund release. This uncertainty should be resolved by a deeper audit of `EscrowSrc.withdraw`.

### Likelihood Explanation
The order's `extraData`/extension (including the arbitrary `tail` postInteraction target) is attacker-controlled content of a self-created order — an unprivileged maker can construct such an order with `allowMultipleFills` set and a malicious custom-postInteraction contract. The only remaining question is whether the underlying (imported, out-of-scope) LOP permits reentrant `fillOrderArgs` calls on the same order within one call stack; this repository provides no defense of its own regardless, since it neither guards `_postInteraction` with `nonReentrant` nor otherwise sequences the external call after state consumption.

### Recommendation
- Move the external call to the custom postInteraction target (lines 94-105) to *after* `lastValidated[key]` has been read and the escrow's immutables (including `hashlock`) have been finalized, or
- Add a `nonReentrant` guard to `_postInteraction` (and `createDstEscrow`) as was done for the analogous MIMO `rebalance` fix, and/or cache `validated` into a local read prior to any external interaction so a reentrant write cannot affect the currently-processing fill.

### Proof of Concept
1. Attacker crafts a maker order with `allowMultipleFills` enabled and a Merkle root of secrets they fully control/know.
2. Order's extension `extraData` includes a `tail` pointing to an attacker contract implementing `IPostInteraction`.
3. A resolver fills part of the order via `fillOrderArgs`; LOP calls `MerkleStorageInvalidator.takerInteraction`, setting `lastValidated[key]` for this fill, then calls `_postInteraction`.
4. `_postInteraction` reaches line 94-105 and calls the attacker's `postInteraction`. Inside that callback, the attacker re-enters the LOP and performs another partial fill of the *same order* for a different chunk, which re-invokes `takerInteraction` and overwrites `lastValidated[key]`.
5. Control returns to the outer `_postInteraction`, which reads the now-overwritten `lastValidated[key]` at line 118 and binds the wrong `hashlock`/validates against the wrong `index` for the outer fill's `EscrowSrc`.

Because this depends on unverified LOP reentrancy semantics and `EscrowSrc.withdraw` access control I did not inspect, I recommend a Devin session with full repository/tooling access to confirm exploitability end-to-end (including whether the imported LOP contract blocks same-order reentrant fills) before treating this as a confirmed Critical/High-severity theft finding.

### Citations

**File:** contracts/BaseEscrowFactory.sol (L31-45)
```text
abstract contract BaseEscrowFactory is IEscrowFactory, SimpleSettlement, MerkleStorageInvalidator {
    using AddressLib for Address;
    using Clones for address;
    using ImmutablesLib for IBaseEscrow.Immutables;
    using SafeERC20 for IERC20;
    using TimelocksLib for Timelocks;

    error InvalidFeeAmounts();

    /// @notice See {IEscrowFactory-ESCROW_SRC_IMPLEMENTATION}.
    address public immutable ESCROW_SRC_IMPLEMENTATION;
    /// @notice See {IEscrowFactory-ESCROW_DST_IMPLEMENTATION}.
    address public immutable ESCROW_DST_IMPLEMENTATION;
    bytes32 internal immutable _PROXY_SRC_BYTECODE_HASH;
    bytes32 internal immutable _PROXY_DST_BYTECODE_HASH;
```

**File:** contracts/BaseEscrowFactory.sol (L94-122)
```text
        if (tail.length > 19) {
            IPostInteraction(address(bytes20(tail))).postInteraction(
                order,
                extension,
                orderHash,
                taker,
                makingAmount,
                takingAmount,
                remainingMakingAmount,
                tail[20:]
            );
        }

        ExtraDataArgs calldata extraDataArgs;
        assembly ("memory-safe") {
            extraDataArgs := add(extraData.offset, superArgsLength)
        }

        bytes32 hashlock;

        if (MakerTraitsLib.allowMultipleFills(order.makerTraits)) {
            uint256 partsAmount = uint256(extraDataArgs.hashlockInfo) >> 240;
            if (partsAmount < 2) revert InvalidSecretsAmount();
            bytes32 key = keccak256(abi.encodePacked(orderHash, uint240(uint256(extraDataArgs.hashlockInfo))));
            ValidationData memory validated = lastValidated[key];
            hashlock = validated.leaf;
            if (!_isValidPartialFill(makingAmount, remainingMakingAmount, order.makingAmount, partsAmount, validated.index)) {
                revert InvalidPartialFill();
            }
```

**File:** contracts/MerkleStorageInvalidator.sol (L45-69)
```text
    function takerInteraction(
        IOrderMixin.Order calldata /* order */,
        bytes calldata extension,
        bytes32 orderHash,
        address /* taker */,
        uint256 /* makingAmount */,
        uint256 /* takingAmount */,
        uint256 /* remainingMakingAmount */,
        bytes calldata extraData
    ) external onlyLOP {
        bytes calldata postInteraction = extension.postInteractionTargetAndData();
        IEscrowFactory.ExtraDataArgs calldata extraDataArgs;
        TakerData calldata takerData;
        assembly ("memory-safe") {
            extraDataArgs := add(postInteraction.offset, sub(postInteraction.length, SRC_IMMUTABLES_LENGTH))
            takerData := extraData.offset
        }
        uint240 rootShortened = uint240(uint256(extraDataArgs.hashlockInfo));
        bytes32 key = keccak256(abi.encodePacked(orderHash, rootShortened));
        bytes32 rootCalculated = takerData.proof.processProofCalldata(
            keccak256(abi.encodePacked(uint64(takerData.idx), takerData.secretHash))
        );
        if (uint240(uint256(rootCalculated)) != rootShortened) revert InvalidProof();
        lastValidated[key] = ValidationData(takerData.idx + 1, takerData.secretHash);
    }
```
