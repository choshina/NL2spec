% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function new_str = revert_underscore(str)
    new_str = regexprep(str, 'underscore', '_');
