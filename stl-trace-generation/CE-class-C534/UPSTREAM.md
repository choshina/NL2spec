# CE-Class provenance

- Upstream repository: <https://github.com/choshina/CEClass>
- Upstream language: MATLAB
- Local implementation: Python/PyTorch port
- Exact source commit: **unknown; provenance action required**
- License: GNU GPL version 3; see `LICENSE`
- Paper: <https://arxiv.org/abs/2601.13743>

The local implementation names upstream MATLAB sources in its documentation,
including `Parser.m`, `MyClassProblemLongBS.m`, and related classification
strategies. Preserve the GPL license and describe significant local changes in
release notes. Before release, compare the port against a pinned upstream
commit and replace the unknown revision above with its full SHA.

## Source annotation convention

Every Python source file carries `SPDX-License-Identifier: GPL-3.0-only` and a
provenance header. The file-level classification in `PROVENANCE.tsv` uses:

- `ceclass-derived-port` for 11 core formula, lattice, strategy, and parameter
  synthesis modules that port named MATLAB classes or shared algorithm logic;
- `port-support-addition` for 24 Python/GPU conversion, data, visualization,
  example, runner, and test modules added around the port in this repository.

This is a functional/lineage classification, not a claim that the Python and
MATLAB files are textually identical. All files remain covered by the bundled
GPL license.

