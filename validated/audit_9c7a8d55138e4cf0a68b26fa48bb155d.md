### Title
Image allowlist bypass via variable expansion mismatch between validation and nesting-VM creation - (File: `executors/instance/instance.go`, `executors/internal/autoscaler/acquisition.go`)

### Summary
`instance.go`'s `Prepare` validates `options.Build.Image.Name` against `AllowedImages` using the **raw, unexpanded** string, but `acquisitionRef.createVMTunnel` later expands CI variable references in that same string via `ExpandValue` before passing it to `nc.Create`. Because `os.Expand`-based expansion happens strictly after the allowlist check, a job can supply an image name containing a variable placeholder whose literal form satisfies a glob pattern in `AllowedImages`, while the runtime-expanded value is an entirely different, disallowed image.

### Finding Description
- `instance.go` `Prepare` (lines 52-66) calls `common.VerifyAllowedImage` on the raw `options.Build.Image.Name`, before `environment.Prepare` is invoked [1](#0-0) .
- `common.VerifyAllowedImage` performs a doublestar glob match of `AllowedImages` patterns against the literal image string, with no awareness of `${VAR}`/`$VAR` syntax [2](#0-1) .
- After the check passes, `instance.go` calls `environment.Prepare`, which eventually reaches `acquisitionRef.createVMTunnel` [3](#0-2) .
- In `createVMTunnel`, when `mapJobImageToVMImage` is true (which it is for the instance executor, set via `autoscaler.Config{MapJobImageToVMImage: true}` in `NewProvider`), the **same raw** `options.Build.Image.Name` is copied into `image`, then expanded with `options.Build.GetAllVariables().ExpandValue(image)` before being passed to `nc.Create(ctx, image, &slot)` [4](#0-3) [5](#0-4) .
- `ExpandValue` uses `os.Expand(value, b.Get)`, substituting `$VAR`/`${VAR}` occurrences with job-variable values, including CI/CD variables the pipeline author fully controls [6](#0-5) .

Exploit flow: an unprivileged pipeline author sets, e.g., `image: "alpine:${INJECT}"` with a CI variable `INJECT` whose value is an arbitrary path segment (job variables are attacker-controlled input, no special permission required). If the runner's `AllowedImages` contains a permissive-looking pattern (e.g. `alpine:*`), the raw literal string `alpine:${INJECT}` matches that glob pattern character-for-character (since `${INJECT}` is just characters to the glob matcher), so `VerifyAllowedImage` passes. However, the value that is actually used to create the nesting VM is the **expanded** string (e.g. `alpine:../../evil-registry/backdoor:latest` or any value `INJECT` is set to), which was never checked against `AllowedImages`. The two functions operate on the same `Build.Image.Name` field at two different points in the same `Prepare` call chain, but one reads it pre-expansion and the other post-expansion — this is a real validate-wrong-value bug, not a TOCTOU across processes.

Existing checks do not stop this: `VerifyAllowedImage` only ever sees the unexpanded string, and `createVMTunnel` performs no re-validation after expansion.

### Impact Explanation
This allows an unprivileged job (pipeline author) to cause the runner's nesting VM allowlist enforcement (`Instance.AllowedImages` / VM isolation) to be circumvented, launching a nesting VM image never vetted by the allowlist. Since VM isolation is a security boundary meant to constrain which images/hosts run in the autoscaler fleet, this undermines the "Runner-enforced restrictions on images ... must hold against user-controlled input" invariant.

### Likelihood Explanation
Preconditions: instance executor with `VMIsolation.Enabled = true` and a non-empty `AllowedImages` list configured on the runner (a realistic hardened configuration), `mapJobImageToVMImage` true (default for the instance executor), and an `AllowedImages` glob pattern broad enough that a literal `${VAR}`-containing string can match it (e.g. any pattern using `*`, which is a common/typical allowlist entry like `myregistry.example.com/*`). The attacker needs only the ability to define an `image:` field and CI/CD variables in their own pipeline — both are standard, unprivileged capabilities. This is fully reproducible and deterministic.

### Recommendation
Perform allowlist validation on the **fully expanded** image name, not the raw string. Concretely, in `instance.go`, expand `options.Build.Image.Name` via `options.Build.GetAllVariables().ExpandValue(...)` before calling `common.VerifyAllowedImage`, and/or re-validate the final `image` value inside `createVMTunnel` immediately after the `ExpandValue` call and before `nc.Create` is invoked, returning an error if it fails the allowlist check.

### Proof of Concept
Go unit test in `executors/internal/autoscaler/acquisition_test.go` style:
1. Construct `options.Config.Instance.AllowedImages = []string{"alpine:*"}`, `options.Config.Autoscaler.VMIsolation.Enabled = true`.
2. Set `options.Build.Image.Name = "alpine:${INJECT}"` and add job variable `INJECT` = `"../evilrepo/backdoor:latest"` (or any string containing a disallowed segment).
3. Call `instance.executor.Prepare(options)` — assert it returns `nil` error (i.e., `VerifyAllowedImage` incorrectly passes on the raw string).
4. Mock/stub `nc.Create` (nestingapi.Client) inside `createVMTunnel` and assert it is invoked with `image == "alpine:../evilrepo/backdoor:latest"` — a value that was never checked against `AllowedImages` — demonstrating the mismatch between the validated value and the value actually used to create the VM.
5. Add a second assertion: manually run `common.VerifyAllowedImage` against the expanded image string and confirm it returns `common.ErrDisallowedImage`, proving the expanded value would have failed the allowlist had it been checked.

### Citations

**File:** executors/instance/instance.go (L52-66)
```go
	if options.Config.Autoscaler.VMIsolation.Enabled && options.Build.Image.Name != "" {
		var allowed []string
		if options.Config.Instance != nil {
			allowed = options.Config.Instance.AllowedImages
		}

		// verify image is allowed
		if err := common.VerifyAllowedImage(common.VerifyAllowedImageOptions{
			Image:         options.Build.Image.Name,
			OptionName:    "images",
			AllowedImages: allowed,
		}, e.BuildLogger); err != nil {
			return err
		}
	}
```

**File:** executors/instance/instance.go (L73-77)
```go
	e.BuildLogger.Println("Preparing instance...")
	e.client, err = environment.Prepare(options.Context, e.BuildLogger, options)
	if err != nil {
		return fmt.Errorf("creating instance environment: %w", err)
	}
```

**File:** executors/instance/instance.go (L139-145)
```go
	return autoscaler.New(executors.DefaultExecutorProvider{
		Creator:          creator,
		FeaturesUpdater:  featuresUpdater,
		DefaultShellName: options.Shell.Shell,
	}, autoscaler.Config{
		MapJobImageToVMImage: true,
	})
```

**File:** common/allowed_images.go (L20-26)
```go
func VerifyAllowedImage(options VerifyAllowedImageOptions, logger buildlogger.Logger) error {
	for _, allowedImage := range options.AllowedImages {
		ok, _ := doublestar.Match(allowedImage, options.Image)
		if ok {
			return nil
		}
	}
```

**File:** executors/internal/autoscaler/acquisition.go (L192-221)
```go
func (ref *acquisitionRef) createVMTunnel(
	ctx context.Context,
	logger buildlogger.Logger,
	nc nestingapi.Client,
	fleetingDialer connector.Client,
	options common.ExecutorPrepareOptions,
) (executors.Client, error) {
	nestingCfg := options.Config.Autoscaler.VMIsolation

	// use nesting config defined image, unless the executor allows for the
	// job image to override.
	image := nestingCfg.Image
	if options.Build.Image.Name != "" && ref.mapJobImageToVMImage {
		image = options.Build.Image.Name
	}

	image = options.Build.GetAllVariables().ExpandValue(image)
	if image == "" {
		return nil, errNoNestingImageSpecified
	}

	logger.Println("Creating nesting VM", image)

	// create vm
	var vm hypervisor.VirtualMachine
	var stompedVMID *string
	var err error
	err = withInit(ctx, options.Config, nc, func() error {
		slot := int32(ref.AcquisitionSlot())
		vm, stompedVMID, err = nc.Create(ctx, image, &slot)
```

**File:** common/spec/variables.go (L153-155)
```go
func (b Variables) ExpandValue(value string) string {
	return os.Expand(value, b.Get)
}
```
