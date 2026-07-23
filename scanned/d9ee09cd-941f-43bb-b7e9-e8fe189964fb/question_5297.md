# Q5297: init_bridge_committee unauthorized package upgrade path

## Question
Can an unprivileged attacker reach `init_bridge_committee` during package upgrade with crafted bridge, active_validator_voting_power, min_stake_participation_percentage, ctx and bypass package authority, compatibility, or upgrade-policy checks so a package changes behavior without the legitimate owner’s authorization?

## Target
- File/function: crates/sui-framework/packages/bridge/sources/bridge.move::init_bridge_committee
- Entrypoint: Package publish or package upgrade transaction with crafted Move bytecode, metadata, or dependency state
- Attacker controls: bridge, active_validator_voting_power, min_stake_participation_percentage, ctx
- Exploit idea: Test whether upgrade capability, dependency graph, linkage state, or compatibility checks can be confused into approving an attacker-controlled package version.
- Invariant to test: Only the authorized upgrade path may change package code or linkage, and every upgrade must preserve the intended compatibility boundary.
- Expected Immunefi impact: Critical — unauthorized package upgrade leading to significant loss of funds or protected-state corruption.
- Fast validation: Build a conflicting upgrade package locally, vary dependencies and policy flags, and see whether the network accepts and executes it.
