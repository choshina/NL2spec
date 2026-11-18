# Vendored Java dependencies

These binaries are inherited from the BUSTLE-style build and are loaded by
`pom.xml` using Maven `system` scope. A binary filename alone is not sufficient
provenance for an archival research artifact.

| File | Declared version | Upstream | Exact build/revision | Checksum | License status |
| --- | --- | --- | --- | --- | --- |
| `JGEA.jar` | 1.3.4 | <https://github.com/ericmedvet/jgea> | unknown | `7a2860d72500f077a3c6d3ddeab4e19c7de198229de21b3df5c575bd55bdc310` | verify upstream release |
| `moonlight.jar` | 1.0-SNAPSHOT | <https://github.com/MoonLightSuite/MoonLight> | unknown | `7473e28c3a3254d8cb6d18c5b2550d689e0557ff1dc1a9df05e6d2ed26d16b30` | verify upstream revision |
| `jblas-1.2.4.jar` | 1.2.4 | <https://github.com/jblas-project/jblas> | unknown | `ba0fa1310f8de4a40d27b974d1da93d20f3fff6d03956bbd4e47d6d5beecf57d` | verify bundled notices |

Before release, prefer reproducible dependency coordinates or source builds.
If binaries remain vendored, replace every `unknown` entry with the full
revision/download URL and add a copy or link to the applicable license and
notices.

