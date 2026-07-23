# Q646: publish_and_register_coins_return_add_coins_on_sui_action unauthorized package upgrade path

## Question
Can an unprivileged attacker reach `publish_and_register_coins_return_add_coins_on_sui_action` during package upgrade with crafted wallet_context, bridge_arg, token_packages_dir, token_ids and bypass package authority, compatibility, or upgrade-policy checks so a package changes behavior without the legitimate owner’s authorization?

## Target
- File/function: crates/sui-bridge/src/utils.rs::publish_and_register_coins_return_add_coins_on_sui_action
- Entrypoint: Package publish or package upgrade transaction with crafted Move bytecode, metadata, or dependency state
- Attacker controls: wallet_context, bridge_arg, token_packages_dir, token_ids
- Exploit idea: Test whether upgrade capability, dependency graph, linkage state, or compatibility checks can be confused into approving an attacker-controlled package version.
- Invariant to test: Only the authorized upgrade path may change package code or linkage, and every upgrade must preserve the intended compatibility boundary.
- Expected Immunefi impact: Critical — unauthorized package upgrade leading to significant loss of funds or protected-state corruption.
- Fast validation: Build a conflicting upgrade package locally, vary dependencies and policy flags, and see whether the network accepts and executes it.
