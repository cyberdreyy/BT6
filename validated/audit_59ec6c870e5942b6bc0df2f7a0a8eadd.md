[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/move-stdlib/sources/configs/features.move (L25-45)
```text
module std::features {
    use std::error;
    use std::signer;
    use std::vector;

    const EINVALID_FEATURE: u64 = 1;
    const EAPI_DISABLED: u64 = 2;
    /// Deployed to production, and disabling is deprecated.
    const EFEATURE_CANNOT_BE_DISABLED: u64 = 3;

    // --------------------------------------------------------------------------------------------
    // Code Publishing

    /// Whether validation of package dependencies is enabled, and the related native function is
    /// available. This is needed because of introduction of a new native function.
    /// Lifetime: transient
    const CODE_DEPENDENCY_CHECK: u64 = 1;

    public fun code_dependency_check_enabled(): bool {
        is_enabled(CODE_DEPENDENCY_CHECK)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1-1)
```text
/**
```
