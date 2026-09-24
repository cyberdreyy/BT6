No vulnerability found for this question.

The reported root cause pattern (no admin function to sweep reward tokens lost to precision/rounding) does not apply to Aera's production contracts. Aera already implements a dedicated `sweep` mechanism across all major versions:

- `v1/AeraVaultV1.sol` has `sweep(address token, uint256 amount)` restricted `onlyOwner`, which blocks only sweeping the pool token itself. [1](#0-0) 
- `v2/Sweepable.sol` provides a generic `sweep` function (including native token recovery) that any inheriting v2 contract can use to recover accidentally-sent or stranded tokens. [2](#0-1) 
- `v3/src/core/Sweepable.sol` provides the same capability, gated by `requiresAuth`, for v3 contracts. [3](#0-2) 

Additionally, the specific reward-streaming contracts referenced in the report (`RewardAccumulator.sol`, `VsTAN.sol`) are part of the unrelated USG Tangent Judging codebase, not present in this Aera repository — there is no analogous `rewardRate`/`REWARDS_DURATION` streaming mechanism in the in-scope Aera contracts (`ProvisionerV2`, `MultiDepositorVault`, `BaseVault`, `PriceAndFeeCalculatorV2`, etc.) that would leave precision dust permanently unrecoverable. The fee/price conversion logic in `v3/src/core/PriceAndFeeCalculatorV2.sol` uses `Math.mulDiv` with explicit rounding directions (`Ceil`/floor) precisely to control precision loss direction, and any residual dust in vaults remains sweepable by the authorized owner via `Sweepable`. [4](#0-3) 

Since Aera already has a built-in, deployed sweep mechanism addressing exactly this class of issue, there is no unresolved analog vulnerability to report.

### Citations

**File:** v1/AeraVaultV1.sol (L626-637)
```text
    /// @inheritdoc IProtocolAPI
    // prettier-ignore
    function sweep(address token, uint256 amount)
        external
        override
        onlyOwner
    {
        if (token == address(pool)) {
            revert Aera__CannotSweepPoolToken();
        }
        IERC20(token).safeTransfer(owner(), amount);
    }
```

**File:** v2/Sweepable.sol (L1-25)
```text
// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.21;

import "@openzeppelin/Ownable2Step.sol";
import "@openzeppelin/SafeERC20.sol";
import "./interfaces/ISweepable.sol";

/// @title Sweepable.
/// @notice Aera Sweepable contract.
/// @dev Allows owner of the contract to restore accidentally send tokens
//       and the chain's native token.
contract Sweepable is ISweepable, Ownable2Step {
    using SafeERC20 for IERC20;

    /// @inheritdoc ISweepable
    function sweep(address token, uint256 amount) external onlyOwner {
        if (token == address(0)) {
            msg.sender.call{value: amount}("");
        } else {
            IERC20(token).safeTransfer(msg.sender, amount);
        }

        emit Sweep(token, amount);
    }
}
```

**File:** v3/src/core/Sweepable.sol (L1-38)
```text
// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.34;

import { ISweepable } from "src/core/interfaces/ISweepable.sol";

import { IERC20 } from "@oz/interfaces/IERC20.sol";
import { SafeERC20 } from "@oz/token/ERC20/utils/SafeERC20.sol";
import { Authority } from "@solmate/auth/Auth.sol";
import { Auth2Step } from "src/core/Auth2Step.sol";

/// @title Sweepable
/// @notice This contract allows the owner of the contract to recover accidentally sent tokens
/// and the chain's native token
abstract contract Sweepable is ISweepable, Auth2Step {
    using SafeERC20 for IERC20;

    constructor(address initialOwner, Authority initialAuthority) Auth2Step(initialOwner, initialAuthority) { }

    ////////////////////////////////////////////////////////////
    //              Public / External Functions               //
    ////////////////////////////////////////////////////////////

    /// @inheritdoc ISweepable
    function sweep(address token, uint256 amount) external requiresAuth {
        if (token == address(0)) {
            // Interactions: send the native token to the owner
            (bool success,) = msg.sender.call{ value: amount }("");
            // Requirements: check that the execution was successful
            require(success, Aera__FailedToSendNativeToken());
        } else {
            // Interactions: transfer the token to the owner
            IERC20(token).safeTransfer(msg.sender, amount);
        }

        // Log the sweep event
        emit Sweep(token, amount);
    }
}
```

**File:** v3/src/core/PriceAndFeeCalculatorV2.sol (L500-517)
```text
    function _convertTokenToUnits(
        address vault,
        IERC20 token,
        uint256 tokenAmount,
        uint256 unitPrice,
        Math.Rounding rounding
    ) internal view returns (uint256 unitsAmount) {
        uint256 numeraireAmount = tokenAmount;
        if (address(token) != NUMERAIRE) {
            if (rounding == Math.Rounding.Ceil) {
                numeraireAmount = _getQuoteCeil(vault, tokenAmount, token, IERC20(NUMERAIRE));
            } else {
                numeraireAmount = ORACLE_REGISTRY.getQuoteForUser(tokenAmount, address(token), NUMERAIRE, vault);
            }
        }

        return Math.mulDiv(numeraireAmount, UNIT_PRICE_PRECISION, unitPrice, rounding);
    }
```
