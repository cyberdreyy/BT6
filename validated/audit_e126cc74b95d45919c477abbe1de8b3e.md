### Title
`MerkleClaim.claim` marks tokens as claimed without checking the ERC20 `transfer` return value, permanently bricking claims for non-standard tokens - (File: contracts/MerkleClaim.sol)

### Summary
`MerkleClaim.claim` sets `hasClaimed[to] = true` and then calls `IERC20Upgradeable(token).transfer(to, amount)` without checking the boolean return value. For ERC20 tokens that signal failure by returning `false` instead of reverting (e.g., temporarily pausable or legacy tokens), the claim is permanently consumed while no tokens are disbursed — the same bug class as the `FootiumPrizeDistributor.claimERC20Prize` finding. Other fund-moving contracts in the repo (`DefaultDistributor.claim`, `IdleCreditVault._transferFundedClaim`, `_transferDefaultRecovery`) already use `SafeERC20`, making `MerkleClaim` the only remaining unchecked-transfer claim surface.

### Finding Description
In `MerkleClaim.sol`:

```solidity
// Set address to claimed
hasClaimed[to] = true;

// Transfer tokens to claimee
IERC20Upgradeable(token).transfer(to, amount);
``` [1](#0-0) 

The effects ordering is: mark claimed → raw `transfer`. Solidity does not revert when a callee returns `false`; the return value is simply ignored. Contrast with `DefaultDistributor.claim`, which uses `IERC20(token).safeTransfer(...)` and therefore reverts on a `false` return, preserving the claim. [2](#0-1) 

Attack/loss scenario (no attacker needed beyond ordinary token behavior; any claimee is unprivileged):
1. `MerkleClaim` is initialized with a merkle root and a token `T`, and `T` is funded to the contract.
2. Token `T` gets paused (or an exchange/blacklist condition applies to the claimee) so `T.transfer` returns `false` rather than reverting.
3. Claimee calls `claim(to, amount, proof)`. The merkle check passes, `hasClaimed[to]` is set, the transfer returns `false`, and the transaction succeeds.
4. Every subsequent `claim` reverts with `AlreadyClaimed()` — the entitlement is permanently burned while the tokens remain in the contract.
5. Recovery depends entirely on `TL_MULTISIG` calling `sweep` after `deployTime + 60 days`, at which point tokens go to a multisig-chosen address, not the claimee's proven allocation. [3](#0-2) 

### Impact Explanation
Permanent freezing of unclaimed claimable tokens. A claimee whose outbound transfer returns `false` loses their merkle entitlement: `hasClaimed` blocks retry, and there is no privileged reset of `hasClaimed` or re-root mechanism. The funds are recoverable only via the 60-day `sweep` by `TL_MULTISIG`, which is discretionary and pays to an arbitrary address. Loss equals the full `amount` proven in the claimee's leaf for every affected claimee (potentially the whole distribution if the token is paused for the entire claim window and users keep claiming).

### Likelihood Explanation
Low-to-medium. It requires the distributed `token` to be a non-standard ERC20 that returns `false` on failure (pausable legacy tokens, blacklistable tokens) and for a transfer to actually fail during a claim — e.g., token paused, or the claimee address being blacklisted by the token. If the token reverts normally, the transaction reverts cleanly and `hasClaimed` is rolled back, so only the non-reverting-failure case is vulnerable. No attacker action is needed; the failure is triggered by token state, and the claimee themselves is the unprivileged caller.

### Recommendation
Use OpenZeppelin `SafeERC20Upgradeable` so a `false` return reverts and rolls back `hasClaimed`:

```solidity
using SafeERC20Upgradeable for IERC20Upgradeable;
...
IERC20Upgradeable(token).safeTransfer(to, amount);
```

Apply the same to `sweep`. Alternatively, wrap the call in `require(IERC20Upgradeable(token).transfer(to, amount), "transfer failed")`. Optionally also allow the multisig to reset `hasClaimed` for recovery, though `safeTransfer` alone fully fixes the reported bug.

### Proof of Concept
Foundry-style PoC sketch (fork or local; `FalseToken` is a minimal ERC20 whose `transfer` returns `false` instead of reverting, e.g., a pausable token):

```solidity
function test_claimBrickedOnFalseReturn() public {
    FalseToken t = new FalseToken();
    address alice = address(0xA11CE);
    uint256 amount = 100e18;

    // Build a 1-leaf merkle tree: leaf = keccak256(bytes.concat(keccak256(abi.encode(alice, amount))))
    bytes32 leaf = keccak256(bytes.concat(keccak256(abi.encode(alice, amount))));

    MerkleClaim mc = new MerkleClaim();
    mc.initialize(leaf, address(t));
    t.mint(address(mc), amount); // fund distributor

    t.setFailTransfers(true);    // simulate paused token: transfer returns false

    bytes32[] memory proof = new bytes32[](0);
    vm.prank(alice);
    mc.claim(alice, amount, proof);          // succeeds despite no disbursement

    assertEq(t.balanceOf(alice), 0);         // nothing received
    assertTrue(mc.hasClaimed(alice));        // claim burned

    t.setFailTransfers(false);               // token unpaused
    vm.prank(alice);
    vm.expectRevert(MerkleClaim.AlreadyClaimed.selector);
    mc.claim(alice, amount, proof);          // permanently unrecoverable
}
```

Note: `FalseToken` is standard PoC scaffolding. The invariant broken is "one receipt → one payout": receipt state (`hasClaimed`) is consumed without a corresponding disbursement, with no in-contract recovery path. [4](#0-3)

### Citations

**File:** contracts/MerkleClaim.sol (L52-67)
```text
  function claim(address to, uint256 amount, bytes32[] calldata proof) external {
    // Throw if address has already claimed tokens
    if (hasClaimed[to]) revert AlreadyClaimed();

    // Verify merkle proof, or revert if not in tree,
    // double keccak is preferred https://github.com/OpenZeppelin/merkle-tree#user-content-fn-1-786ed85226485d53b8a7834b144c1642
    bytes32 leaf = keccak256(bytes.concat(keccak256(abi.encode(to, amount))));
    bool isValidLeaf = MerkleProofUpgradeable.verify(proof, merkleRoot, leaf);
    if (!isValidLeaf) revert NotInMerkle();

    // Set address to claimed
    hasClaimed[to] = true;

    // Transfer tokens to claimee
    IERC20Upgradeable(token).transfer(to, amount);
  }
```

**File:** contracts/MerkleClaim.sol (L69-75)
```text
  function sweep(address to) public {
    require(msg.sender == TL_MULTISIG);
    // allow sweep after 60 days
    require(block.timestamp > deployTime + 60 days);
    address _token = token;
    IERC20Upgradeable(_token).transfer(to, IERC20Upgradeable(_token).balanceOf(address(this)));
  }
```

**File:** contracts/DefaultDistributor.sol (L39-40)
```text
    tranche.safeTransferFrom(msg.sender, address(this), trancheBal);
    IERC20(token).safeTransfer(_to, trancheBal * rate / ONE_TRANCHE);
```
