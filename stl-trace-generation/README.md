# STL Trace Generation

This project generates example signal traces from requirements written in Signal Temporal Logic (STL). Given an STL requirement, it asks the Z3 SMT solver for one trace that satisfies the requirement and one trace that violates it, then plots both sets of traces in MATLAB. The intended use case is requirement debugging: a controls or CPS engineer can inspect concrete behaviors that pass or fail a temporal requirement.

This directory is a composite integration, not a standalone original codebase.
It contains project-specific integration code alongside research software from
CE-Class, Automatic Trace Generation for Signal Temporal Logic (ATG), and
Breach. See [`../THIRD_PARTY.md`](../THIRD_PARTY.md) before redistribution.

## Provenance

- `CE-class-C534/` is a Python/PyTorch port of named classes and algorithms
  from the GPL-3.0 CE-Class implementation. Its bundled `LICENSE` applies.
- `STL_TO_SMT/` contains the ATG-lineage STL-to-SMT implementation. Several
  files identify J. Kapinski and P. Prabhakar as authors. The exact upstream
  repository, revision, and software license have not yet been recovered; see
  [`STL_TO_SMT/UPSTREAM.md`](STL_TO_SMT/UPSTREAM.md).
- `GenExTraces/GenerateCaseDatasetFromCsv.m` is the project-specific adapter
  that connects formulas, CE-Class targets, the ATG-derived translator, and
  oracle-labelled repair datasets.
- `breach/` is a vendored snapshot of the Breach MATLAB toolbox and remains
  under its own bundled BSD-style license.

No ownership of the original CE-Class, ATG, or Breach implementations is
claimed by this repository.

## Project Layout

- `GenExTraces/`: runnable MATLAB examples and sample `.stl` requirement files.
- `STL_TO_SMT/`: MATLAB implementation that translates STL formulas into SMT-LIB queries for Z3.
- `STL_TO_SMT/infix2prefix/`: small Java parser used to convert arithmetic predicates from infix to prefix form.
- `CE-class-C534/`: GPL-licensed Python CE-Class port used by the repair loop.
- `breach/`: bundled Breach dependency.

## Requirements

Install these before running the examples:

- MATLAB. The code uses Breach MATLAB APIs; GNU Octave is not guaranteed to work.
- Java JDK, for compiling/running `Infix2Prefix`.
- Z3, available as `z3` on your shell `PATH`.
- A C/C++ compiler configured for MATLAB `mex` if you need to rebuild Breach MEX files. Some Linux `.mexa64` binaries are already included, but they may not match your OS or MATLAB version.

On macOS with Homebrew, Z3 can be installed with:

```sh
brew install z3
```

On Ubuntu, the repository-level `scripts/bootstrap_ubuntu.sh` installs Java and Z3.

Check the required command-line tools:

```sh
java -version
javac -version
z3 --version
```

## Setup

From the project root:

```sh
javac STL_TO_SMT/infix2prefix/Infix2Prefix.java \
      STL_TO_SMT/infix2prefix/Lexer.java \
      STL_TO_SMT/infix2prefix/YYParser.java
```

Then start MATLAB:

```sh
matlab
```

Inside MATLAB, `cd` to the project root:

```matlab
cd('/path/to/stl-trace-generation')
```

Initialize the MATLAB paths:

```matlab
addpath(genpath('breach'))
addpath('STL_TO_SMT')
addpath(fullfile('STL_TO_SMT', 'infix2prefix'))
InitBreach
```

If `InitBreach` reports missing or incompatible MEX files, run:

```matlab
InstallBreach
InitBreach
```

## Run an Example

The easiest example is `GenTracesExample_02.m`, which uses the generic requirements in `GenExTraces/SampleReqs.stl`.

In MATLAB:

```matlab
cd('/path/to/stl-trace-generation/GenExTraces')
GenTracesExample_02
```

The same example can be run from a portable shell command with:

```sh
cd /path/to/stl-trace-generation
"${MATLAB_BIN:-matlab}" -batch "restoredefaultpath; addpath(genpath('breach')); addpath('STL_TO_SMT'); addpath(fullfile('STL_TO_SMT','infix2prefix')); InitBreach; cd('GenExTraces'); GenTracesExample_02;" -logfile matlab_run.log
```

The script loads `SampleReqs.stl`, selects several predefined STL requirements, sends SMT-LIB queries to Z3, reads `model.txt`, and opens figures with:

- green/satisfying traces, and
- red/violating traces.

For examples that generate multiple distinct traces using blocking constraints, run:

```matlab
cd('/path/to/stl-trace-generation/GenExTraces')
GenTracesExample_03
```

## Typical Programmatic Use

```matlab
cd('/path/to/stl-trace-generation/GenExTraces')
STL_ReadFile('SampleReqs.stl');

k = 3;      % number of switching points
mT = 5;     % time horizon

h = smt_plot_examples(Val_high_req, mT, k, ...
    'SignalPref', 'linear', ...
    'RealValuedBlocking', true, ...
    'NumExamples', 2, ...
    'Delta', 10);
```

Important options for `smt_plot_examples`:

- `SignalPref`: `'linear'` for piecewise-linear traces or `'const'` for piecewise-constant traces.
- `NumExamples`: number of satisfying/violating traces to request.
- `RealValuedBlocking`: add constraints so repeated examples differ in real-valued signal assignments.
- `BooleanValuedBlocking`: add constraints so repeated examples differ in Boolean formula assignments.
- `Delta`: distance used for real-valued blocking constraints.

## Outputs

During each run, the tool writes these files in the current MATLAB working directory:

- `stl_translation.smt2`: the generated SMT-LIB query sent to Z3.
- `model.txt`: the model returned by Z3.

These files are overwritten on subsequent runs.

## Troubleshooting

- `z3: command not found`: install Z3 and make sure `z3` is on the shell path visible to MATLAB.
- `Undefined function or variable 'STL_ReadFile'`: add `breach/` to the MATLAB path and run `InitBreach`.
- `Could not find or load main class Infix2Prefix`: compile the Java files and add `STL_TO_SMT/infix2prefix` to the MATLAB path.
- Path errors inside `STL_Parse_Table`: run the latest project version; it should use the local `STL_TO_SMT/infix2prefix` directory rather than a hard-coded absolute path.
- No figures appear: make sure MATLAB has a display available and that Z3 returned `sat` in `model.txt`.
