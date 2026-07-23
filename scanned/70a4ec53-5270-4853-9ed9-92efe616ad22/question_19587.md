# Q19587: update_for_publication unauthorized package upgrade path

## Question
Can an unprivileged attacker reach `update_for_publication` during package upgrade with crafted package_version_id, original_package_id, resolved_linkage and bypass package authority, compatibility, or upgrade-policy checks so a package changes behavior without the legitimate owner’s authorization?

## Target
- File/function: sui-execution/latest/sui-adapter/src/static_programmable_transactions/linkage/resolved_linkage.rs::update_for_publication
- Entrypoint: Package publish or package upgrade transaction with crafted Move bytecode, metadata, or dependency state
- Attacker controls: package_version_id, original_package_id, resolved_linkage
- Exploit idea: Test whether upgrade capability, dependency graph, linkage state, or compatibility checks can be confused into approving an attacker-controlled package version.
- Invariant to test: Only the authorized upgrade path may change package code or linkage, and every upgrade must preserve the intended compatibility boundary.
- Expected Immunefi impact: Critical — unauthorized package upgrade leading to significant loss of funds or protected-state corruption.
- Fast validation: Build a conflicting upgrade package locally, vary dependencies and policy flags, and see whether the network accepts and executes it.
