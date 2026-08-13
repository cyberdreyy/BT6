# Q3652: lending_pool_clone_emode: initializer-like path can be re-entered or retargeted [two-banks-from-different-groups] [rollback]

## Question
Can an unprivileged attacker call `lending_pool_clone_emode` with two banks from different groups sharing compatible layouts so `lending_pool_clone_emode` re-initializes or retargets protected state that should be one-time or one-object-only, breaking `emode cloning must remain authorized and source/destination-bound so no attacker can copy privileged risk settings into the wrong bank` and causing `High: unsafe live leverage configuration through unauthorized state mutation`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/emode_clone.rs` / `lending_pool_clone_emode`
- Entrypoint: `lending_pool_clone_emode`
- Attacker controls: two banks from different groups sharing compatible layouts
- Exploit idea: Audit init/copy/clone paths for idempotence and one-time-use assumptions that are not fully enforced on-chain. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: emode cloning must remain authorized and source/destination-bound so no attacker can copy privileged risk settings into the wrong bank
- Expected Immunefi impact: High: unsafe live leverage configuration through unauthorized state mutation
- Fast validation: Repeat initialization/clone with attacker-controlled accounts and assert already-initialized or foreign-owned state cannot be hijacked. Force the late failure branch and assert every protected field fully rolls back.
