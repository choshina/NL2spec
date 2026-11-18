# Third-party provenance and notices

This document records the source and role of third-party research software in
this repository. Academic citation and software licensing are separate
requirements: citing a paper does not by itself grant permission to copy or
redistribute its implementation.

The repository was imported with a single local `Initial commit`; upstream
commit history was not preserved. Consequently, exact source revisions marked
as **unknown** below must be recovered from the original acquisition records or
confirmed by comparison with upstream repositories before an archival release.

## BUSTLE

- Upstream: <https://github.com/pigozzif/BUSTLESTLLearningFromData>
- Exact imported revision: **unknown**
- Audited comparison reference:
  `e945ad648036a05d788fe72f92b1ce6a41ddd98b` (2024-02-25)
- Local paths: `src/main/java/it/units/malelab/learningstl/`, `grammars/`,
  portions of `pom.xml`, `libs/`
- Relationship: adapted and substantially extended
- Upstream license found: **none in the identified repository**
- Paper: Federico Pigozzi, Laura Nenzi, and Eric Medvet, “BUSTLE: A
  Versatile Tool for the Evolutionary Learning of STL Specifications from
  Data,” *Evolutionary Computation* 33(1), 2025,
  <https://doi.org/10.1162/evco_a_00347>

Project-specific extensions include semantic repair mutations, deterministic
repair-mutant enumeration, formula parsing and replay utilities,
responsibility-guided localization, repair fitness metrics, and integration
with the iterative Python repair workflow. See the Java-side
`PROVENANCE.md` and `PROVENANCE.tsv` for the per-file boundary used in this
repository.

Because no explicit upstream software license was found, this repository does
not assert permission to relicense or redistribute the BUSTLE-derived files.
Before public archival, obtain written permission or an upstream license from
the copyright holders, or publish project-specific changes separately from a
user-obtained BUSTLE checkout.

## CE-Class

- Upstream: <https://github.com/choshina/CEClass>
- Exact source revision used for the port: **unknown**
- Local path: `stl-trace-generation/CE-class-C534/`
- Relationship: Python/PyTorch port of named MATLAB classes and algorithms,
  with project-specific adapters
- License: GNU General Public License version 3; bundled at
  `stl-trace-generation/CE-class-C534/LICENSE`
- Paper: Zhenya Zhang, Parv Kapoor, Jie An, and Eunsuk Kang,
  “Counterexample Classification for Signal Temporal Logic Specifications,”
  arXiv:2601.13743v2, accepted by EMSOFT 2026,
  <https://arxiv.org/abs/2601.13743>

The local Python implementation explicitly identifies methods as ports of
upstream files such as `Parser.m` and `MyClassProblemLongBS.m`; it is therefore
treated as a GPL-covered derivative implementation rather than merely an
independent use of ideas from the paper.

## Automatic Trace Generation for Signal Temporal Logic

- Identified paper implementation lineage: Pavithra Prabhakar, Ratan Lal,
  and James Kapinski
- Exact source repository and imported revision: **unknown**
- Local path: `stl-trace-generation/STL_TO_SMT/`
- Relationship: source snapshot modified for the integrated repair workflow
- Upstream license found in the local snapshot: **none**
- Paper: “Automatic Trace Generation for Signal Temporal Logic,” 2018 IEEE
  Real-Time Systems Symposium, pp. 208–217,
  <https://doi.org/10.1109/RTSS.2018.00038>

Several local MATLAB file headers name J. Kapinski and P. Prabhakar and carry
2017 dates. These notices must be retained. Before redistributing this
directory, recover an upstream license or obtain permission from the relevant
copyright holders. `GenExTraces/GenerateCaseDatasetFromCsv.m` is documented as
the project-specific integration layer that calls this translator and Breach.

## Breach

- Upstream: <https://github.com/decyphir/breach>
- Exact imported revision: **unknown**
- Local path: `stl-trace-generation/breach/`
- Relationship: vendored third-party dependency, with local compatibility
  changes possible
- License: bundled BSD-style three-clause license at
  `stl-trace-generation/breach/LICENSE`
- Citation: Alexandre Donzé, “Breach, A Toolbox for Verification and
  Parameter Synthesis of Hybrid Systems,” CAV 2010,
  <https://doi.org/10.1007/978-3-642-14295-6_17>

Breach itself bundles components under additional licenses, including
SUNDIALS and optimization utilities. Their directory-level license files
remain applicable and must be retained in source and binary distributions.

## Binary dependencies

The repository tracks `libs/JGEA.jar`, `libs/moonlight.jar`, and
`libs/jblas-1.2.4.jar`. See `libs/README.md`. Exact source
revisions and checksums should be replaced with verified release information
before an archival release. Removing a binary from Maven Central or another
site does not remove its license obligations.

## Benchmark data

The benchmark records are derived from public research datasets, but public
availability and academic citation do not by themselves establish a uniform
redistribution licence for the normalized records in this repository.

- **DeepSTL**: source corpus and artifact at
  <https://github.com/JieHE-2020/DeepSTL>. The public repository identifies the
  ICSE 2022 artifact but does not expose a top-level software/data licence in
  its repository listing. The local benchmark contains normalized formulas,
  natural-language variants, and generated candidates rather than a verbatim
  copy of the full upstream archive. Confirm redistribution terms with the
  dataset authors before an unrestricted public data release.
- **NASA FRET LPC**: source project at
  <https://github.com/NASA-SW-VnV/fret>. The upstream repository includes a
  NASA licence document; the requirements remain attributed to their source.
- **ARCH-COMP**: properties originate from the public falsification benchmark
  releases and competition reports. Individual benchmark models and auxiliary
  repositories can have different licences; this artifact redistributes the
  normalized properties and author-written English texts, not the executable
  benchmark models.
- **STDP**: formulas originate from `bfz111/STDP-Dataset`. Record the exact
  dataset revision and licence before an unrestricted public data release.

The sampling protocol and the distinction between source requirements,
generated candidates, and retained repair instances are documented in
`benchmarks/README.md`.

## Project-specific code

The repair orchestration under `scripts/`, Python utilities under
`stl_epoch_project/`, benchmark adapters, and newly introduced Java repair
classes contain project-specific contributions except where an individual
file says otherwise. No repository-wide license is granted to those files by
the root licensing notice. Choose and add a license for original contributions
only after the BUSTLE/ATG distribution strategy has been resolved.

## Release checklist

- [ ] Record the exact BUSTLE source commit and redistribution permission.
- [ ] Record the CE-Class commit used as the basis of the Python port.
- [ ] Recover the ATG source location, revision, and software license or
      written permission.
- [ ] Record the exact Breach revision and any local patches.
- [ ] Verify source, version, checksum, and license for every vendored binary.
- [ ] Confirm redistribution terms and exact revisions for DeepSTL and STDP.
- [ ] Archive a release and add its version/DOI to `CITATION.cff` and
      `CITATIONS.bib`.
- [ ] Update the CE-Class BibTeX entry when the EMSOFT proceedings DOI is
      available.

