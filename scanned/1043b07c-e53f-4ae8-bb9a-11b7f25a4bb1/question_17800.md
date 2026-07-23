# Q17800: into_value package-triggered crash or invariant failure

## Question
Can an unprivileged attacker reach `into_value` with crafted package bytes, module metadata, type layouts, dependencies, and upgrade parameters during package publish, upgrade, or execution and trigger a panic, fatal assertion, or validator invariant-violation path on unmodified software?

## Target
- File/function: external-crates/move/crates/move-vm-runtime/src/execution/values/values_impl.rs::into_value
- Entrypoint: Package publish or package upgrade transaction with crafted Move bytecode, metadata, or dependency state
- Attacker controls: package bytes, module metadata, type layouts, dependencies, and upgrade parameters
- Exploit idea: Look for malformed-but-accepted package state that later violates internal assumptions during verification or execution.
- Invariant to test: Adversarial package bytes and metadata must be rejected or safely handled without killing validator or fullnode processes.
- Expected Immunefi impact: Low or Medium — transaction-triggered validator invariant violation or wider liveness impact if the crash is persistent.
- Fast validation: Generate boundary-case local packages, submit them repeatedly, and record whether execution aborts cleanly or kills the process.
