# STL-to-SMT provenance

This directory contains an STL-to-SMT trace-generation implementation
associated with the method published as:

Pavithra Prabhakar, Ratan Lal, and James Kapinski, “Automatic Trace
Generation for Signal Temporal Logic,” 2018 IEEE Real-Time Systems Symposium,
pp. 208–217. <https://doi.org/10.1109/RTSS.2018.00038>

Local file headers identify J. Kapinski and P. Prabhakar and carry 2017 dates.

- Exact upstream repository: **unknown**
- Exact upstream revision: **unknown**
- Upstream software license: **not present in this snapshot**
- Local changes: integration with Breach, portable path handling, repeated
  trace generation, blocking constraints, and repair-dataset generation

Academic citation does not supply software redistribution permission. Retain
all existing author notices. Before publishing this source snapshot, recover
its original distribution terms or obtain written permission. If that cannot
be done, remove this directory from the public artifact and provide an
installer that asks the user to obtain the dependency separately, or replace
it with an independently implemented and licensed translator.

## Source annotation convention

The machine-readable `PROVENANCE.tsv` classifies every bundled file. MATLAB
source files carry a conservative header stating `ATG-lineage source;
original/local edit boundary is unknown`. This wording is intentional: without
the original repository and revision, the repository cannot truthfully claim
that an untraceable change belongs exclusively to STL Repair.

Generated JFlex/Bison Java output retains its generator and GPL notices rather
than receiving a conflicting ownership header. The project-specific caller
`../GenExTraces/GenerateCaseDatasetFromCsv.m` is labelled separately as an STL
Repair integration component.

