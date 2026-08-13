# Q3692: lending_pool_clone_emode: config path trusts caller-chosen remaining accounts too much [source-and-destination-with-divergent] [rollback]

## Question
Can an unprivileged attacker use `lending_pool_clone_emode` with source and destination with divergent existing emode contexts so `lending_pool_clone_emode` applies a protected configuration change using caller-chosen auxiliary accounts, violating `emode cloning must remain authorized and source/destination-bound so no attacker can copy privileged risk settings into the wrong bank` and leading to `High: unsafe live leverage configuration through unauthorized state mutation`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/emode_clone.rs` / `lending_pool_clone_emode`
- Entrypoint: `lending_pool_clone_emode`
- Attacker controls: source and destination with divergent existing emode contexts
- Exploit idea: Look for config flows that pull oracle, metadata, or derived objects from remaining accounts without fully binding them. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: emode cloning must remain authorized and source/destination-bound so no attacker can copy privileged risk settings into the wrong bank
- Expected Immunefi impact: High: unsafe live leverage configuration through unauthorized state mutation
- Fast validation: Swap candidate auxiliary accounts and assert the config path cannot mutate anything unless every auxiliary object matches the canonical target. Force the late failure branch and assert every protected field fully rolls back.
