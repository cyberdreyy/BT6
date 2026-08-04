### Title
Pre-execution shell metacharacter injection via `ExpandValue` in `stepDispatch`'s release-step handling - ([File: common/build_step_dispatch.go])

### Summary
`stepDispatch` textually substitutes job variable values into a `release` step's script lines using `build.GetAllVariables().ExpandValue(s)` *before* the resulting string is handed to the shell as script source text, rather than letting the shell perform runtime variable expansion. Because `ExpandValue` is a literal `os.Expand`-based text substitution (`common/spec/variables.go:153-155`), any job variable whose value contains shell metacharacters (`;`, `` ` ``, `$(...)`) becomes live shell syntax once the expanded line reaches `CommandProcessor.writeNormalCommand`, which writes the string verbatim into the generated script with no escaping [1](#0-0) .

### Finding Description
`stepDispatch` builds the `script` StepInput for `builtin://script_legacy` from `build.Steps`. For the `release` step specifically, each script line is passed through `build.GetAllVariables().ExpandValue(s)` and the result overwrites the original line in place [2](#0-1) .

`ExpandValue` is implemented as `os.Expand(value, b.Get)` [3](#0-2) , i.e. a pure Go string substitution that replaces `$VAR`/`${VAR}` tokens with the variable's raw value as text, with no shell-escaping or quoting applied.

The expanded, already-substituted string is placed into `schema.StepInputs["script"]` [4](#0-3) , consumed by `builtinFunc.Run` in `functions/script_legacy/script_legacy.go`, and fed to `ScriptGenerator.GenerateScript` [5](#0-4) , which iterates each command through `CommandProcessor.ProcessCommand` [6](#0-5) . For single-line commands, `writeNormalCommand` writes the command text directly into the generated shell script buffer unescaped [1](#0-0) .

This differs meaningfully from normal shell variable expansion. When a script line like `echo $VAR` is executed by a shell natively, the shell parses the command line into tokens *first*, and variable substitution happens afterward during execution — so a value like `; rm -rf /` stored in `$VAR` becomes inert argument text to `echo`, not a new command, because bash does not re-parse post-substitution text for command separators. Here, however, the substitution happens in Go *before* the shell ever sees the line, directly editing the source text that will be parsed by the shell. This turns any shell metacharacter in the variable's value into live script syntax: e.g., a release-step script line `echo $VAR` with `VAR` set to `; rm -rf /` becomes the literal script line `echo ; rm -rf /`, which the shell parses as two separate commands.

No escaping, quoting, or sanitization occurs anywhere between `ExpandValue` and the point where `writeNormalCommand` embeds the string into the generated script. `CommandFormatter.FormatLogLine` (used only for a trace/log echo line) and the `posix_escape` option govern how the *echoed log representation* is quoted, but they do not alter or sanitize the actual command line that gets executed on line 54 of `command_processor.go`.

### Impact Explanation
An unprivileged pipeline author who can set the value of any job variable referenced by `$VAR`/`${VAR}` syntax within a `release` step's `Script` array (e.g., via `variables:` in `.gitlab-ci.yml`, or any variable — masked or not, project/group/instance-level, provided the value/content is attacker-influenced) can inject arbitrary shell syntax that executes as separate commands beyond the authored script line, when `FF_USE_SCRIPT_TO_STEP_MIGRATION` is enabled. Since the payload is already running inside the job's own execution context (an unprivileged pipeline author already controls arbitrary script execution in their own job), the scoped impact here is command execution equivalent to what the job could already do — this is not a sandbox-escape or cross-job/cross-project boundary violation. It only becomes a real security issue if the variable's value originates from a source the job author does NOT fully control (e.g., a predefined CI/CD variable holding an external, less-trusted value, or a variable populated indirectly by another actor/service), or if it affects processing of masking, since the substituted text is invisible to the normal script line that appears in `.gitlab-ci.yml` and could execute unexpected commands during release automation without an explicit line in the pipeline config authorizing it.

### Likelihood Explanation
Feasibility requires: (1) `FF_USE_SCRIPT_TO_STEP_MIGRATION` enabled, (2) a `release` step is present, (3) at least one `Script` line for that step references a variable via `$VAR`/`${VAR}`, and (4) that variable's value is attacker-influenced and contains shell metacharacters. All of these are realistic in ordinary pipeline configuration (release step scripts frequently reference `$CI_COMMIT_TAG`, `$RELEASE_DESCRIPTION`, or custom variables), making this readily reproducible under the stated feature-flag precondition.

### Recommendation
Do not pre-expand variables into shell script text via Go-level string substitution. Instead, either (a) let the shell perform variable expansion natively at execution time by keeping `$VAR` references literal in the generated script and exporting variables into the process environment (as is already done for other job variables via `builtinCtx.GetJobVars()` in `script_legacy.go:164-166`), or (b) if pre-expansion into inline text is required, apply strict shell quoting/escaping (e.g., POSIX single-quote escaping) to each substituted variable value before embedding it into the script line, so metacharacters cannot introduce new command boundaries.

### Proof of Concept
Go unit test in `common/build_step_dispatch_test.go`:
```go
func TestStepDispatch_ReleaseStepVariableExpansionDoesNotInjectCommands(t *testing.T) {
    build := &Build{
        JobResponse: JobResponse{
            Variables: JobVariables{
                {Key: "VAR", Value: "; rm -rf /", Public: true},
            },
        },
        Steps: Steps{
            {
                Name:   "release",
                Script: StepScript{"echo $VAR"},
            },
        },
    }
    // ... set up executor/shell mocks ...
    handled, steps := stepDispatch(build, executor, <release build stage>)
    require.True(t, handled)
    script := steps[0].Inputs["script"].([]string)
    // Assert the expanded line is a single inert argument to echo,
    // NOT parsed as two shell commands.
    assert.Equal(t, []string{"echo ; rm -rf /"}, script)
    generated := internal.NewScriptGenerator(cfg).GenerateScript(script)
    assert.NotContains(t, generated, "\nrm -rf /\n",
        "expanded variable content must not create a new command boundary")
}
```
Expected (failing) assertion: the generated script contains `rm -rf /` as a distinct, independently-executable shell statement rather than as literal text passed to `echo`, demonstrating command injection via variable expansion ahead of shell parsing.

### Citations

**File:** functions/script_legacy/internal/command_processor.go (L49-56)
```go
func (p *CommandProcessor) writeNormalCommand(buf *strings.Builder, command string) {
	logLine := p.formatter.FormatLogLine(command)
	buf.WriteString(logLine)
	buf.WriteString("\n")

	buf.WriteString(command)
	buf.WriteString("\n")

```

**File:** common/build_step_dispatch.go (L54-61)
```go
		for _, step := range build.Steps {
			if StepToBuildStage(step) == stage {
				script = append(script, step.Script...)
				if step.Name == "release" {
					for i, s := range step.Script {
						script[i] = build.GetAllVariables().ExpandValue(s)
					}
				}
```

**File:** common/build_step_dispatch.go (L75-87)
```go
		return true, []schema.Step{
			{
				Name: func(s string) *string { return &s }("user_script"),
				Step: "builtin://script_legacy",
				Inputs: schema.StepInputs{
					"script":           script,
					"debug_trace":      build.IsDebugTraceEnabled(),
					"posix_escape":     true,
					"check_for_errors": build.IsFeatureFlagOn(featureflags.EnableBashExitCodeCheck),
					"trace_sections":   build.IsFeatureFlagOn(featureflags.ScriptSections),
				},
			},
		}
```

**File:** common/spec/variables.go (L153-155)
```go
func (b Variables) ExpandValue(value string) string {
	return os.Expand(value, b.Get)
}
```

**File:** functions/script_legacy/script_legacy.go (L156-157)
```go
	generator := internal.NewScriptGenerator(generatorConfig)
	script := generator.GenerateScript(commands)
```

**File:** functions/script_legacy/internal/script_generator.go (L59-61)
```go
	for i, cmd := range commands {
		g.processor.ProcessCommand(&buf, i, cmd)
	}
```
