# SPDX-License-Identifier: GPL-3.0-only
# Provenance: repository-specific support for the CE-Class Python port.
# Distributed with the port under GPL-3.0; see UPSTREAM.md.


from ceclass.formula.stl_node import STLNode

try:
    from ceclass.formula.converter import to_stlcgpp
except ImportError as _converter_import_error:
    def to_stlcgpp(*args, **kwargs):
        raise RuntimeError(
            "stlcgpp conversion requires optional torch/stlcgpp dependencies"
        ) from _converter_import_error
