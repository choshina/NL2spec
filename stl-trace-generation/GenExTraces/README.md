# Repair trace-generation integration

`GenerateCaseDatasetFromCsv.m` is the repository-specific integration layer
for generating SAT and violation traces used by the iterative STL repair
workflow. It converts supported formula syntax, configures signal domains,
calls the ATG-derived translator in `../STL_TO_SMT/`, uses `../breach/`, and
writes oracle-labelled datasets.

This directory should be cited as part of STL Repair. The underlying ATG and
Breach methods must also be cited when exercised. See
[`../../THIRD_PARTY.md`](../../THIRD_PARTY.md) and
[`../STL_TO_SMT/UPSTREAM.md`](../STL_TO_SMT/UPSTREAM.md).

