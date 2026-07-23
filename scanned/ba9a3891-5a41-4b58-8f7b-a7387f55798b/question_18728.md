# Q18728: all_package_dependencies_except type or layout confusion

## Question
Can a crafted package publish or transaction reach `all_package_dependencies_except` with attacker-controlled except and make the system interpret the same bytes as two incompatible types, objects, or ownership states, leading to unauthorized transfer, destruction, or custody escape?

## Target
- File/function: external-crates/move/crates/move-vm-runtime/src/shared/linkage_context.rs::all_package_dependencies_except
- Entrypoint: Package publish or package upgrade transaction with crafted Move bytecode, metadata, or dependency state
- Attacker controls: except
- Exploit idea: Search for deserialized layout assumptions that are weaker than runtime type or ownership expectations.
- Invariant to test: A serialized object, value, or type layout must have exactly one valid interpretation throughout verification and execution.
- Expected Immunefi impact: Critical — state corruption or loss of funds through type confusion in package verification or runtime loading.
- Fast validation: Construct mutated layouts or generic instantiations locally and check whether verification and runtime disagree on the same value.
